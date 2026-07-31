"""Worker AI-report command orchestration.

This is the domain owner for progress/done/blocked/error/ask/derive/ready/
verify/name/reply reports. The server supplies runtime integrations explicitly.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from ..config import log
from ..direct_message_mirrors import (
    ask_recipient_is_user,
    ask_task_labels_for_owner_recipient,
    save_direct_ask_mirror,
)
from ..external_tickets import (
    ExternalTicketError,
    build_completion_comment,
    post_ticket_comment,
    run_external_ticket_operation,
)
from ..mcp_canonical import canonical_tool_name
from ..state import task_counts_as_done, task_is_closed


_REVIEW_GATE_ACTION = "feature/review"
_WORKER_COMPLETE_FOLLOW_UP_OPEN = "Worker Complete — Follow-up Open"
TORQUE_AI_MCP_REPORT_ACTIONS = frozenset({
    "progress", "done", "blocked", "error", "ask", "derive",
    "ready", "verify", "name",
})
_CANONICAL_AI_REPORT_TOOL_BY_ACTION = {
    action: canonical_tool_name("torque_" + action)
    for action in TORQUE_AI_MCP_REPORT_ACTIONS
}
TORQUE_AI_MCP_REPORT_TOOL_NAMES = frozenset({
    *(
        "mcp__torque__" + tool_name
        for tool_name in _CANONICAL_AI_REPORT_TOOL_BY_ACTION.values()
    ),
    *(
        "mcp__torque__torque_" + action
        for action in TORQUE_AI_MCP_REPORT_ACTIONS
    ),
})
# Private compatibility name used by the extracted implementation body.
_TORQUE_AI_MCP_REPORT_ACTIONS = TORQUE_AI_MCP_REPORT_ACTIONS


def _prior_review_task_ids_for_agent(state, task, agent_id: str) -> list[str]:
    """Return prior feature/review task ids assigned to ``agent_id``.

    Review routing is allowed to reuse the reviewer who found a blocker, but
    that assignment must remain distinguishable from a fresh independent
    review.  This history lookup is structural: derive prose is not a routing
    contract and is deliberately not parsed for reviewer exclusions.
    """
    agent_id = str(agent_id or "").strip()
    if not state or not task or not agent_id:
        return []
    task_id = str(getattr(task, "id", "") or "").strip()
    result = []
    for candidate in state.board_get_chain(task_id):
        action_name = str(
            getattr(candidate, "action_name", "") or ""
        ).strip().lower()
        if action_name != _REVIEW_GATE_ACTION:
            continue
        reviewer_id = str(
            getattr(candidate, "agent_id", "") or ""
        ).strip()
        if reviewer_id != agent_id:
            continue
        candidate_id = str(getattr(candidate, "id", "") or "").strip()
        if candidate_id:
            result.append(candidate_id)
    return result


def _reviewer_reuse_assignment(
        *,
        reviewer_id: str,
        prior_review_task_ids: list[str],
        selection_source: str) -> dict:
    """Build the durable, reader-facing prior-reviewer reuse record."""
    return {
        "kind": "prior_reviewer_reuse",
        "reviewer_id": str(reviewer_id or "").strip(),
        "prior_review_task_ids": list(prior_review_task_ids),
        "selection_source": (
            str(selection_source or "").strip()
            or "existing_agent_target"
        ),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


def _apply_reviewer_reuse_assignment(task, assignment: dict,
                                     actor_name: str) -> None:
    """Persist reader-facing reuse metadata on the derived review task."""
    evidence = dict(getattr(task, "completion_evidence", {}) or {})
    evidence["reviewer_assignment"] = assignment
    task.completion_evidence = evidence
    labels = list(getattr(task, "labels", []) or [])
    if "torque:reviewer-reused" not in labels:
        labels.append("torque:reviewer-reused")
    task.labels = labels
    reviewer_id = assignment["reviewer_id"]
    prior_ids = ", ".join(assignment["prior_review_task_ids"])
    task.messages.append({
        "timestamp": time.time(),
        "action": "reviewer_reused",
        "message": (
            "Prior reviewer reuse: "
            f"reviewer {reviewer_id} previously reviewed this task chain in "
            f"{prior_ids}. This assignment is not a fresh reviewer."
        ),
        "agent_name": actor_name,
    })


@dataclass(slots=True)
class AIReportCommandRuntime:
    state: Any
    action_mgr: Any
    worktree_mgr: Any
    board_sync_manager: Any
    bridge: Any
    dispatch_command: Any
    panel_event: Any
    panel_log: Any
    resolve_base_dir: Any
    ai_derive_parent_task: Any
    append_mcp_message: Any
    apply_verification_report: Any
    auto_resolve_product_proposal_roots_and_enqueue: Any
    capture_auto_resume_targets: Any
    checkpoint_message: Any
    checkpoint_on_report: Any
    checkpoint_worktree_with_submodules: Any
    close_agent_session_only: Any
    derive_handoff_accepted: Any
    find_reusable_review_fix_task: Any
    handle_engineer_reply: Any
    inherit_assigned_engineer_for_derived_task: Any
    maybe_apply_review_required_gate: Any
    maybe_auto_close_root_done_agents: Any
    maybe_auto_resume_targets: Any
    maybe_reject_stale_base_review_derive: Any
    nearest_ancestor_agent_for_action_stage: Any
    ownership_engineer_id_for_dispatch_source: Any
    prior_live_reviewer_agent_for_chain: Any
    promote_task_for_active_report: Any
    pump_auto_dispatch_queue: Any
    record_derive_dispatch_shape_metric: Any
    record_review_verdict_evidence: Any
    record_task_boundary: Any
    record_task_completion_evidence_snapshot: Any
    refresh_reused_derived_task: Any
    reject_completion_with_open_descendants: Any
    reject_mandatory_review_done_without_ship: Any
    reject_missing_deliverable: Any
    reject_pending_review: Any
    resolve_agent_id: Any
    resolve_ai_report_task: Any
    resolve_feature_review_derive_stream_backstop_task: Any
    review_event_message: Any
    shared_review_checkpoint_block_reason: Any


async def handle_ai_report_command(
    data: dict,
    runtime: AIReportCommandRuntime,
) -> dict | None:
    state = runtime.state
    action_mgr = runtime.action_mgr
    worktree_mgr = runtime.worktree_mgr
    board_sync_manager = runtime.board_sync_manager
    bridge = runtime.bridge
    handle_command = runtime.dispatch_command
    _panel_event = runtime.panel_event
    panel_log = runtime.panel_log
    _resolve_base_dir = runtime.resolve_base_dir
    _ai_derive_parent_task = runtime.ai_derive_parent_task
    _append_mcp_message = runtime.append_mcp_message
    _apply_verification_report = runtime.apply_verification_report
    _auto_resolve_product_proposal_roots_and_enqueue = runtime.auto_resolve_product_proposal_roots_and_enqueue
    _capture_auto_resume_targets = runtime.capture_auto_resume_targets
    _checkpoint_message = runtime.checkpoint_message
    _checkpoint_on_report = runtime.checkpoint_on_report
    _checkpoint_worktree_with_submodules = runtime.checkpoint_worktree_with_submodules
    _close_agent_session_only = runtime.close_agent_session_only
    _derive_handoff_accepted = runtime.derive_handoff_accepted
    _find_reusable_review_fix_task = runtime.find_reusable_review_fix_task
    _handle_engineer_reply = runtime.handle_engineer_reply
    _inherit_assigned_engineer_for_derived_task = runtime.inherit_assigned_engineer_for_derived_task
    _maybe_apply_review_required_gate = runtime.maybe_apply_review_required_gate
    _maybe_auto_close_root_done_agents = runtime.maybe_auto_close_root_done_agents
    _maybe_auto_resume_targets = runtime.maybe_auto_resume_targets
    _maybe_reject_stale_base_review_derive = runtime.maybe_reject_stale_base_review_derive
    _nearest_ancestor_agent_for_action_stage = runtime.nearest_ancestor_agent_for_action_stage
    _ownership_engineer_id_for_dispatch_source = runtime.ownership_engineer_id_for_dispatch_source
    _prior_live_reviewer_agent_for_chain = runtime.prior_live_reviewer_agent_for_chain
    _promote_task_for_active_report = runtime.promote_task_for_active_report
    _pump_auto_dispatch_queue = runtime.pump_auto_dispatch_queue
    _record_derive_dispatch_shape_metric = runtime.record_derive_dispatch_shape_metric
    _record_review_verdict_evidence = runtime.record_review_verdict_evidence
    _record_task_boundary = runtime.record_task_boundary
    _record_task_completion_evidence_snapshot = runtime.record_task_completion_evidence_snapshot
    _refresh_reused_derived_task = runtime.refresh_reused_derived_task
    _reject_completion_with_open_descendants = runtime.reject_completion_with_open_descendants
    _reject_mandatory_review_done_without_ship = runtime.reject_mandatory_review_done_without_ship
    _reject_missing_deliverable = runtime.reject_missing_deliverable
    _reject_pending_review = runtime.reject_pending_review
    _resolve_agent_id = runtime.resolve_agent_id
    _resolve_ai_report_task = runtime.resolve_ai_report_task
    _resolve_feature_review_derive_stream_backstop_task = runtime.resolve_feature_review_derive_stream_backstop_task
    _review_event_message = runtime.review_event_message
    _shared_review_checkpoint_block_reason = runtime.shared_review_checkpoint_block_reason
    result = None

    cell_id = data.get("cell_id", "")
    action = data.get("action", "")
    message = data.get("message", "")
    task_id = data.get("task_id", "")
    terminal_declaration = data.get("terminal_declaration", "")
    deviation_statement = data.get("deviation_statement", "")
    deviation_reason = data.get("deviation_reason", "")

    cell = state.agents.get(cell_id)
    if not cell:
        result = {"type": "error",
                  "message": f"Cell {cell_id} not found"}
    else:
        task = _resolve_ai_report_task(
            state,
            cell,
            task_id=task_id,
        )
        if (
                action == "done"
                and not task
                and "final review verdict" in str(message or "").lower()
        ):
            amendable_task_ids = []
            for candidate in state.board_tasks.values():
                evidence = getattr(candidate, "completion_evidence", {}) or {}
                review = evidence.get("review", {}) if isinstance(evidence, dict) else {}
                if (
                        isinstance(review, dict)
                        and str(review.get("agent_id", "") or "").strip()
                        == str(cell.id or "").strip()
                        and str(review.get("verdict", "") or "").strip()
                        == "unknown"
                ):
                    amendable_task_ids.append(candidate.id)
            if amendable_task_ids:
                task_list = ", ".join(sorted(amendable_task_ids)[:5])
                result = {
                    "type": "error",
                    "message": (
                        "A completed review verdict is immutable; repeating "
                        "task_complete cannot amend it. Use "
                        "review_verdict_amend(task=\"TASK_ID\", verdict=\"ship\", "
                        "reason=\"...\") as the original reviewer. "
                        f"Eligible task(s): {task_list}"
                    ),
                }
        derive_stream_backstop = False
        if (
            not task
            and not str(task_id or "").strip()
            and action == "derive"
            and str(
                data.get("action_name", "") or ""
            ).strip().lower() == _REVIEW_GATE_ACTION
        ):
            task = await _resolve_feature_review_derive_stream_backstop_task(
                state,
                cell,
                worktree_mgr,
            )
            derive_stream_backstop = bool(task)
        resume_targets = _capture_auto_resume_targets(
            state,
            task=task,
            group=(task.group if task else cell.group) or "",
        )

        def _add_label(t, label):
            if label not in t.labels:
                t.labels.append(label)

        def _save_task(t):
            from datetime import datetime, timezone
            t.updated_at = datetime.now(
                timezone.utc).isoformat()
            state._emit("task_upsert", **asdict(t))
            state._db_save_task(t)

        def _cascade_done(task_id):
            """Complete done task ancestors via state-layer logic."""
            state.board_cascade_done(task_id)

        def _append_mcp(c, act, msg=""):
            _append_mcp_message(c, act, msg)
            # Emit a live `mcp_call_append` delta for the
            # canonical worker-report tool call on the SAME
            # broadcast that carries this report's
            # event_append + agent_upsert. Without this, the
            # only `mcp_call_append` for worker reports
            # would come from the `/events` PostToolUse hook
            # — a separate `state.broadcast()` ~30-100ms
            # later (Claude Code dispatches the hook after
            # the MCP tool returns). That second broadcast
            # misses rAF coalesce in the frontend and
            # produces a second full DOM rebuild of the
            # engineer panel per call (visible flicker,
            # mid-type selection loss, scroll-anchor
            # churn). The `/events` capture clause
            # downstream suppresses its own emission for
            # tool names in `_TORQUE_AI_MCP_REPORT_TOOL_NAMES`
            # so we don't double-emit; persistence still
            # writes, so the on-demand `cmd=mcp_calls`
            # fetch keeps working and codex workers (no
            # PostToolUse hooks) keep getting the live
            # delta from this path.
            if act in _TORQUE_AI_MCP_REPORT_ACTIONS:
                try:
                    _now = time.time()
                    row = {
                        "cursor": 0,
                        "idempotency_key": "",
                        "cell_id": c.id,
                        "tool_name": (
                            "mcp__torque__"
                            + _CANONICAL_AI_REPORT_TOOL_BY_ACTION[act]
                        ),
                        "hook_event_name": "PostToolUse",
                        "session_id": getattr(
                            c, "session_id", "") or "",
                        "appended_at": _now,
                        "received_at": _now,
                        "duration_ms": None,
                        "success": act != "error",
                        "error": (msg if act == "error" else ""),
                        "args": {"message": str(msg)} if msg else {},
                        "args_redacted": False,
                        "result": None,
                        "result_redacted": True,
                        "agent_name": c.name,
                        "agent_slug": getattr(c, "slug", ""),
                        "agent_kind": getattr(c, "kind", ""),
                        "group": getattr(c, "group", ""),
                    }
                    state._emit(
                        "mcp_call_append",
                        group=row["group"],
                        call=row,
                    )
                except Exception:
                    log.exception(
                        "Failed to emit synthetic "
                        "mcp_call_append for ai_report"
                    )

        def _append_task_msg(t, act, msg, agent_name):
            """Append to the task's persisted activity log."""
            if t:
                t.messages.append({
                    "timestamp": time.time(),
                    "action": act,
                    "message": msg,
                    "agent_name": agent_name,
                })

        def _record_history_msg(c, act, msg="", task_override=None):
            """Persist to agent_messages history table."""
            state.history_record_message(
                c.id, act, msg,
                task_id=(
                    task_override.id if task_override
                    else (task.id if task else "")
                ))

        def _acknowledge_block_replies(t, c, report_action):
            """Receipt is the worker's next report after a delivered ruling."""
            if not t or not c:
                return False
            changed = False
            now = time.time()
            for entry in list(getattr(t, "messages", []) or []):
                if (not isinstance(entry, dict)
                        or entry.get("action") != "block_reply"
                        or str(entry.get("worker_id", "") or "") != c.id
                        or entry.get("delivery_state") != "delivered"
                        or entry.get("acknowledged_at")):
                    continue
                entry["acknowledged_at"] = now
                entry["acknowledged_by_action"] = report_action
                entry["delivery_reason"] = "worker_acknowledged"
                entry["delivery_updated_at"] = now
                message_id = str(entry.get("reply_message_id", "") or "")
                if message_id:
                    state.mark_direct_message_read(
                        message_id, read_at=now, reader_id=c.id, emit=True)
                changed = True
            if changed:
                _save_task(t)
            return changed

        async def _drain_auto_dispatch_queue(group_name: str):
            if not group_name:
                return
            await _pump_auto_dispatch_queue(
                state,
                handle_command,
                _panel_event,
                group=group_name,
            )

        async def _derive_is_available(t) -> bool:
            """Whether this task's action currently offers a derive transition."""
            if not t:
                return False
            base_dir = cell.worktree_repo_root \
                or cell.directory \
                or await _resolve_base_dir(t.group or cell.group)
            transitions = action_mgr.get_transitions(
                t.action_name, base_dir)
            return any(
                isinstance(transition, dict)
                and transition.get("action")
                for transition in transitions
            )

        def _has_terminal_declaration(value) -> bool:
            """Require the deliberate terminal statement, not diligence prose."""
            text = str(value or "").strip().lower()
            return (
                "no further work" in text
                and "will not" in text
                and "derive" in text
            )

        async def _reject_missing_terminal_declaration(t, action_name):
            if not await _derive_is_available(t):
                return None
            if _has_terminal_declaration(terminal_declaration):
                return None
            corrected_call = (
                'torque_done(message="brief summary", '
                'terminal_declaration="No further work is needed; '
                'I will not derive after this.")'
                if action_name == "done"
                else 'torque_ready(terminal_declaration="No further work '
                'is needed; I will not derive after this.")'
            )
            return {
                "type": "error",
                "message": (
                    f"Cannot mark task {action_name}: terminal_declaration "
                    "is required because this task has an available derive "
                    f"transition. Call {corrected_call}"
                ),
                "task_id": t.id if t else "",
            }

        def _completion_message(default: str) -> str:
            """Persist the required terminal decision with the completion."""
            summary = str(message or "").strip() or default
            declaration = str(terminal_declaration or "").strip()
            if declaration:
                return (
                    f"{summary}\n\n"
                    f"Terminal declaration: {declaration}"
                )
            return summary

        def _complete_task_or_leave_follow_up_open(t) -> bool:
            """Close a task only when its derived follow-up chain is resolved.

            A worker may finish its own work before a derived task does.  In
            that case the worker report is terminal, while the board task
            remains visibly active so the existing cascade remains the sole
            authority that advances the chain.
            """
            if not t:
                return False
            if state.task_has_unresolved_descendants(t.id):
                t.agent_id = ""
                t.status = _WORKER_COMPLETE_FOLLOW_UP_OPEN
                _save_task(t)
                return False
            if not task_counts_as_done(t):
                state.board_move_task(t.id, "Done")
                _auto_resolve_product_proposal_roots_and_enqueue(
                    state,
                    t,
                    board_sync_manager=board_sync_manager,
                )
            t.agent_id = ""
            t.status = ""
            _save_task(t)
            _cascade_done(t.id)
            return True

        def _notify_unblocked_dependents(t):
            if not t:
                return
            for dependent in state.board_get_dependents(t.id):
                if not task_is_closed(dependent) \
                        and state.board_deps_met(dependent):
                    _panel_event(
                        "task_unblocked", "",
                        "", dependent.group,
                        f"Task '{dependent.task[:60]}'"
                        " is now unblocked",
                        task_id=dependent.id)

        if action in {
            "progress", "done", "blocked", "error",
            "ask", "derive", "ready", "verify",
        }:
            state.mark_agent_progress(cell)

        if action != "blocked":
            _acknowledge_block_replies(task, cell, action)

        if result and result.get("type") == "error":
            pass  # auto-resolve failed; skip action

        elif action in {"progress", "blocked", "error",
                         "verify", "derive", "ask"}:
            if not derive_stream_backstop:
                _promote_task_for_active_report(state, cell, task)

        if (
            not (result and result.get("type") == "error")
            and action == "done"
        ):
            deliverable_rejection = (
                _reject_missing_deliverable(task, "done")
            )
            if deliverable_rejection:
                result = deliverable_rejection
        if (
            not (result and result.get("type") in (
                "error", "deliverable_missing"))
            and action == "done"
        ):
            review_rejection = (
                _reject_pending_review(task, "done")
            )
            if review_rejection:
                result = review_rejection
        if (
            not (result and result.get("type") == "error")
            and not (result
                     and result.get("type")
                     in ("deliverable_missing",
                         "review_required"))
            and action == "done"
        ):
            base_dir = cell.worktree_repo_root \
                or cell.directory \
                or await _resolve_base_dir(
                    task.group if task else cell.group)
            mandatory_review_rejection = (
                _reject_mandatory_review_done_without_ship(
                    state,
                    action_mgr,
                    cell,
                    task,
                    base_dir=base_dir,
                )
            )
            if mandatory_review_rejection:
                result = mandatory_review_rejection
            else:
                rejected = await _reject_missing_terminal_declaration(
                    task, "done")
                if rejected:
                    result = rejected
                elif not result:
                    async def _checkpoint_for_review_gate():
                        if not (
                            cell.worktree_path
                            and cell.cell_type == "agent"
                            and cell.worktree_auto_checkpoint
                        ):
                            return
                        try:
                            n = cell.worktree_checkpoints + 1
                            cp_msg = (
                                f"torque: checkpoint {n} — "
                                f"{cell.name}"
                            )
                            if message:
                                cp_msg = f"{cp_msg}\n\n{message}"
                            elif cell.last_summary:
                                cp_msg = (
                                    f"{cp_msg}\n\n"
                                    f"{cell.last_summary.strip()}"
                                )
                            sha = await _checkpoint_worktree_with_submodules(
                                cell,
                                cp_msg,
                            )
                            if sha:
                                state._db_save_agent(cell)
                        except Exception:
                            log.exception(
                                "review gate checkpoint failed for"
                                " '%s'", cell.name)

                    gate_result = await _maybe_apply_review_required_gate(
                        state,
                        action_mgr,
                        worktree_mgr,
                        handle_command,
                        _panel_event,
                        cell=cell,
                        task=task,
                        base_dir=base_dir,
                        force_skip_review=bool(
                            data.get("force_skip_review")),
                        skip_reason=data.get(
                            "review_skip_reason", ""),
                        checkpoint_for_gate=
                            _checkpoint_for_review_gate,
                        append_task_msg=_append_task_msg,
                        record_history_msg=_record_history_msg,
                    )
                    if gate_result:
                        result = gate_result

        if result and result.get("type") in (
                "error", "deliverable_missing",
                "review_required"):
            pass

        elif action == "done":
            completion_message = _completion_message("Done")
            cell.activity = ""
            cell.activity_detail = ""
            cell.needs_attention = False
            cell.error_message = ""
            if completion_message:
                cell.last_summary = completion_message
            cell.current_task_id = ""
            _append_mcp(cell, "done", completion_message)
            _append_task_msg(task, "done",
                             completion_message, cell.name)
            _record_history_msg(
                cell, "done", completion_message)
            if task:
                state.history_complete_task(
                    cell.id, task.id, "done")
            if task:
                await _record_task_boundary(
                    task, cell, completion_message
                )
                _record_task_completion_evidence_snapshot(
                    state,
                    task,
                    cell=cell,
                    action="done",
                    message=completion_message,
                    deviation_statement=deviation_statement,
                    deviation_reason=deviation_reason,
                    board_sync_manager=board_sync_manager,
                )
                review_verdict = _record_review_verdict_evidence(
                    state,
                    task,
                    cell=cell,
                    source_action="done",
                    message=completion_message,
                    append_task_msg=_append_task_msg,
                    record_history_msg=_record_history_msg,
                )
            else:
                review_verdict = {}
            state._emit_agent(cell)
            # Auto-checkpoint on done. The session_end hook
            # callback (_on_agent_session_end, wired at
            # server.py:1541) already checkpoints, but it
            # only fires when Claude Code's
            # Stop/SessionEnd/idle_prompt hook reaches us —
            # racy and skipped when the agent calls
            # torque_done mid-turn. Running the same
            # checkpoint synchronously here ensures dirty
            # work lands on the branch before the MCP
            # reply returns, mirroring the pre-merge
            # checkpoint in worktree_merge.
            if (cell.worktree_path
                    and cell.cell_type == "agent"
                    and cell.worktree_auto_checkpoint):
                block_reason = (
                    _shared_review_checkpoint_block_reason(
                        state,
                        cell,
                    )
                )
                if block_reason:
                    log.info(
                        "Skipping done auto-checkpoint: %s",
                        block_reason,
                    )
                else:
                    try:
                        cp_msg = _checkpoint_message(cell)
                        sha = await _checkpoint_worktree_with_submodules(
                            cell,
                            cp_msg,
                        )
                        if sha:
                            state._db_save_agent(cell)
                    except Exception:
                        log.exception(
                            "done auto-checkpoint failed for"
                            " '%s'", cell.name)
            if task:
                task_completed = _complete_task_or_leave_follow_up_open(task)
                if data.get("push_external") \
                        and (task.provider or task.external_url):
                    try:
                        posted = await run_external_ticket_operation(
                            post_ticket_comment,
                            task,
                            comment=build_completion_comment(
                                task.task, completion_message),
                        )
                        _append_task_msg(
                            task, "external_comment",
                            posted, "torque")
                        _save_task(task)
                        result = {
                            "type": "external_comment_posted",
                            "task_id": task.id,
                            "message": posted,
                        }
                    except ExternalTicketError as exc:
                        _append_task_msg(
                            task, "external_error",
                            str(exc), "torque")
                        _save_task(task)
                        result = {
                            "type": "warning",
                            "message": str(exc),
                            "task_id": task.id,
                        }
                if task_completed:
                    _notify_unblocked_dependents(task)
            if review_verdict:
                _panel_event(
                    "review_verdict", cell.id,
                    cell.name, cell.group,
                    _review_event_message(review_verdict),
                    task_id=task.id if task else "")
            _panel_event(
                "task_completed", cell.id,
                cell.name, cell.group,
                completion_message,
                task_id=task.id if task else "")
            await _maybe_auto_resume_targets(
                state,
                handle_command,
                _panel_event,
                targets=resume_targets,
                group=task.group if task else cell.group,
            )
            await _drain_auto_dispatch_queue(
                task.group if task else cell.group
            )
            if task:
                await _maybe_auto_close_root_done_agents(
                    state,
                    task,
                    action_mgr=action_mgr,
                    resolve_base_dir=_resolve_base_dir,
                    close_agent=_close_agent_session_only,
                )

        elif action == "blocked":
            cell.needs_attention = True
            cell.activity = "waiting"
            cell.activity_detail = message
            _append_mcp(cell, "blocked", message)
            _append_task_msg(task, "blocked",
                             message, cell.name)
            if task and task.messages:
                # The durable correlation survives a daemon restart and is
                # intentionally kept in the task's persisted activity log.
                task.messages[-1].update({
                    "block_id": "block-" + uuid.uuid4().hex[:12],
                    "agent_id": cell.id,
                    "agent_session_id": str(getattr(cell, "agent_session_id", "") or ""),
                    "session_id": str(getattr(cell, "session_id", "") or ""),
                    "reply_message_id": "",
                })
            _record_history_msg(cell, "blocked", message)
            state._emit_agent(cell)
            if task:
                _add_label(task, "torque:blocked")
                _save_task(task)
            _panel_event(
                "agent_blocked", cell.id,
                cell.name, cell.group, message,
                task_id=task.id if task else "")
            state.recompute_task_health()

        elif action == "error":
            cell.error_message = message
            cell.needs_attention = True
            _append_mcp(cell, "error", message)
            _append_task_msg(task, "error",
                             message, cell.name)
            _record_history_msg(cell, "error", message)
            state._emit_agent(cell)
            if task:
                _add_label(task, "torque:error")
                _save_task(task)
            _panel_event(
                "agent_error", cell.id,
                cell.name, cell.group, message,
                task_id=task.id if task else "")
            state.recompute_task_health()

        elif action == "progress":
            cell.activity_detail = message
            if cell.needs_attention:
                cell.needs_attention = False
            _append_mcp(cell, "progress", message)
            _append_task_msg(task, "progress",
                             message, cell.name)
            _record_history_msg(cell, "progress", message)
            state._emit_agent(cell)
            if task:
                _save_task(task)
            # Auto-checkpoint on progress (throttled)
            await _checkpoint_on_report(cell, message)
            # Panel event — replace last progress
            # for this agent to avoid flooding
            pe = panel_log.replace_last(
                "agent_progress", cell.id,
                agent_name=cell.name,
                group=cell.group,
                message=message)
            state._emit("event_append", **pe)
            state.recompute_task_health()

        elif action == "verify":
            if not task:
                result = {"type": "error",
                          "message": "No linked task to verify"}
            else:
                finalization_review_result = None
                finalization_review = data.get("finalization_review")
                if finalization_review is not None:
                    required_review_keys = {
                        "gate_id", "verdict", "has_blocking_issues",
                        "required_follow_up_resolved", "boundary",
                    }
                    if (not isinstance(finalization_review, dict)
                            or set(finalization_review) != required_review_keys):
                        return {
                            "type": "error",
                            "message": "finalization_review must contain exactly the typed gate, verdict, blocker, follow-up, and boundary fields",
                        }
                    try:
                        finalization_review_result = state.record_finalization_review(
                            task.id,
                            gate_id=finalization_review["gate_id"],
                            verdict=finalization_review["verdict"],
                            has_blocking_issues=finalization_review["has_blocking_issues"],
                            required_follow_up_resolved=finalization_review["required_follow_up_resolved"],
                            boundary=finalization_review["boundary"],
                            executed=True,
                        )
                    except (TypeError, ValueError) as exc:
                        return {"type": "error", "message": str(exc)}
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
                ):
                    if key in data:
                        payload[key] = data[key]
                root_task = None
                root_id = task.pipeline_root_id or ""
                if root_id and root_id != task.id:
                    root_task = state.board_tasks.get(root_id)
                verify_msg, _root_task = _apply_verification_report(
                    task,
                    payload,
                    cell.name,
                    _save_task,
                    root_task=root_task,
                )
                if task_counts_as_done(task):
                    _record_task_completion_evidence_snapshot(
                        state,
                        task,
                        cell=cell,
                        action="verify",
                        message=verify_msg,
                        board_sync_manager=board_sync_manager,
                    )
                if (
                        _root_task
                        and task_counts_as_done(_root_task)):
                    _record_task_completion_evidence_snapshot(
                        state,
                        _root_task,
                        cell=cell,
                        action="verify",
                        message=verify_msg,
                        board_sync_manager=board_sync_manager,
                    )
                _append_mcp(cell, "verify", verify_msg)
                _append_task_msg(task, "verify",
                                 verify_msg, cell.name)
                _record_history_msg(cell, "verify", verify_msg)
                state._emit_agent(cell)
                _panel_event(
                    "task_verification_updated",
                    cell.id, cell.name, cell.group,
                    verify_msg, task_id=task.id,
                )
                result = {
                    "type": (
                        "finalization_review_recorded"
                        if finalization_review_result is not None
                        else "verification_updated"
                    ),
                    "task_id": task.id,
                    "message": verify_msg,
                }
                if finalization_review_result is not None:
                    result["finalization"] = finalization_review_result
                await _maybe_auto_resume_targets(
                    state,
                    handle_command,
                    _panel_event,
                    targets=resume_targets,
                    group=task.group if task else cell.group,
                )

        elif action == "ready":
            deliverable_rejection = (
                _reject_missing_deliverable(task, "ready")
            )
            review_rejection = (
                None if deliverable_rejection
                else _reject_pending_review(task, "ready")
            )
            rejected = (
                deliverable_rejection
                or review_rejection
                or await _reject_missing_terminal_declaration(
                    task, "ready")
            )
            if rejected:
                result = rejected
            else:
                completion_message = _completion_message("Ready")
                cell.activity = ""
                cell.activity_detail = "ready"
                cell.needs_attention = False
                cell.error_message = ""
                cell.current_task_id = ""
                _append_mcp(cell, "ready", completion_message)
                _append_task_msg(task, "ready",
                                 completion_message, cell.name)
                _record_history_msg(
                    cell, "ready", completion_message)
                if task:
                    state.history_complete_task(
                        cell.id, task.id, "ready")
                    await _record_task_boundary(
                        task, cell, completion_message
                    )
                    _record_task_completion_evidence_snapshot(
                        state,
                        task,
                        cell=cell,
                        action="ready",
                        message=completion_message,
                        deviation_statement=deviation_statement,
                        deviation_reason=deviation_reason,
                        board_sync_manager=board_sync_manager,
                    )
                state._emit_agent(cell)
                if task:
                    task_completed = _complete_task_or_leave_follow_up_open(task)
                    if task_completed:
                        _notify_unblocked_dependents(task)
                _panel_event(
                    "task_completed", cell.id,
                    cell.name, cell.group,
                    "Ready (task completed)",
                    task_id=task.id if task else "")
                await _maybe_auto_resume_targets(
                    state,
                    handle_command,
                    _panel_event,
                    targets=resume_targets,
                    group=task.group if task else cell.group,
                )
                await _drain_auto_dispatch_queue(
                    task.group if task else cell.group
                )

        elif action == "derive":
            # Derive a new task and dispatch it
            act_name = data.get("action_name", "")
            act_vars = data.get("action_vars", {})
            derive_group = data.get("group", "")
            reuse_self = data.get("reuse_self", False)
            target_agent = (
                data.get("target_agent", "") or ""
            ).strip()
            reviewer_target_source = (
                "explicit_target" if target_agent else ""
            )
            reviewer_reuse = None
            if (
                task
                and not target_agent
                and str(act_name or "").strip().lower()
                == _REVIEW_GATE_ACTION
            ):
                prior_reviewer = \
                    _prior_live_reviewer_agent_for_chain(
                        state,
                        task,
                    )
                if prior_reviewer:
                    target_agent = prior_reviewer.id
                    reviewer_target_source = "automatic_chain_reuse"
            is_auto_review_gate = bool(
                data.get("_review_gate")
            ) and act_name == _REVIEW_GATE_ACTION

            if not task:
                result = {"type": "error",
                          "message":
                              "No linked task to derive from"}
            elif (
                    task_counts_as_done(task)
                    or task.status == _WORKER_COMPLETE_FOLLOW_UP_OPEN
            ):
                result = {
                    "type": "error",
                    "message": (
                        "Cannot derive from a completed task; continue "
                        "from the derived follow-up task instead"
                    ),
                    "task_id": task.id,
                }
            elif not message:
                result = {"type": "error",
                          "message":
                              "Derive requires a description"}
            else:
                # Validate transition
                base_dir = cell.worktree_repo_root \
                    or cell.directory \
                    or await _resolve_base_dir(
                        task.group)
                cur_transitions = \
                    action_mgr.get_transitions(
                        task.action_name, base_dir)
                valid_targets = [
                    t["action"] for t in cur_transitions
                    if isinstance(t, dict)
                    and t.get("action")]
                if cur_transitions and act_name \
                        and act_name not in valid_targets \
                        and not is_auto_review_gate:
                    result = {
                        "type": "error",
                        "message":
                            f"Action '{task.action_name}'"
                            f" cannot transition to "
                            f"'{act_name}'. Valid: "
                            f"{', '.join(valid_targets)}"}
                else:
                    # Check depth limit
                    new_depth = task.pipeline_depth + 1
                    act_meta = \
                        action_mgr.load_action(
                            act_name, base_dir) \
                        if act_name else None
                    max_d = (
                        (act_meta or {}).get("max_depth")
                        or state.global_settings
                            .max_pipeline_depth
                        or 0)
                    stale_base_rejection = None
                    if not (max_d and new_depth > max_d):
                        stale_base_rejection = (
                            await _maybe_reject_stale_base_review_derive(
                                worktree_mgr,
                                cell,
                                act_name,
                            )
                        )
                    if max_d and new_depth > max_d:
                        cell.needs_attention = True
                        state._emit_agent(cell)
                        if task:
                            _add_label(task,
                                       "torque:depth-limit")
                            _save_task(task)
                        result = {
                            "type": "error",
                            "message":
                                f"Pipeline depth limit "
                                f"({max_d}) reached"}
                    elif stale_base_rejection:
                        result = stale_base_rejection
                    else:
                        # Keep parent in In Progress;
                        # update its status from transition
                        cell.activity = ""
                        cell.activity_detail = ""
                        cell.needs_attention = False
                        cell.error_message = ""
                        state._emit_agent(cell)
                        # Determine status from transition
                        derive_status = ""
                        if cur_transitions and act_name:
                            for tr in cur_transitions:
                                if isinstance(tr, dict) \
                                        and tr.get("action") \
                                        == act_name:
                                    derive_status = tr.get(
                                        "status", "")
                                    break
                        if (not derive_status
                                and is_auto_review_gate):
                            derive_status = "On Review"
                        if not derive_status and act_name:
                            derive_status = act_name
                        # Update parent task status
                        task.status = derive_status
                        _save_task(task)
                        # Propagate status to root
                        root_id_s = \
                            task.pipeline_root_id \
                            or task.id
                        if root_id_s != task.id:
                            root_t = \
                                state.board_tasks.get(
                                    root_id_s)
                            if root_t:
                                root_t.status = \
                                    derive_status
                                _save_task(root_t)
                        # Create derived task
                        grp = derive_group \
                            or task.group
                        root_id = \
                            task.pipeline_root_id \
                            or task.id
                        derive_parent_task = \
                            _ai_derive_parent_task(
                                state,
                                task,
                            )
                        derive_parent_task_id = (
                            derive_parent_task.id
                            if derive_parent_task else task.id
                        )
                        derive_desc = data.get(
                            "description", "")
                        assigned_engineer_id = (
                            _inherit_assigned_engineer_for_derived_task(task)
                        )
                        reusable_task = _find_reusable_review_fix_task(
                            state,
                            task,
                            act_name,
                        )
                        reused_existing_task = reusable_task is not None
                        # Mandatory-review pre-approval bypass
                        # (TORQUE:256). When a reviewer derives a
                        # fix via a ``pre_approved: true``
                        # transition, stamp the derived task with
                        # the reviewer's task id so its
                        # ``torque_done`` gate resolves clean.
                        derive_pre_approved_by = ""
                        if cur_transitions and act_name:
                            for tr in cur_transitions:
                                if isinstance(tr, dict) \
                                        and tr.get("action") \
                                        == act_name \
                                        and tr.get("pre_approved"):
                                    derive_pre_approved_by = task.id
                                    break
                        new_task = reusable_task
                        if not new_task:
                            new_task = state.board_add_task(
                                task=message,
                                group=grp,
                                lane="Backlog",
                                action_name=act_name,
                                action_vars=act_vars,
                                labels=["torque:derived"],
                                parent_task_id=derive_parent_task_id,
                                pipeline_depth=new_depth,
                                pipeline_root_id=root_id,
                                description=derive_desc,
                                assigned_engineer_id=assigned_engineer_id,
                                pre_approved_by=derive_pre_approved_by,
                            )
                        elif reused_existing_task:
                            _refresh_reused_derived_task(
                                new_task,
                                message=message,
                                description=derive_desc,
                                action_vars=act_vars,
                            )
                            new_task.parent_task_id = (
                                derive_parent_task_id
                            )
                            new_task.pipeline_root_id = root_id
                            # Reuse path: rewrite
                            # pre_approved_by from the
                            # selected transition so a
                            # blocking-fix re-derive onto a
                            # task that previously carried
                            # a pre-approval bypass clears
                            # it (and vice-versa).
                            new_task.pre_approved_by = (
                                derive_pre_approved_by
                            )
                            _inherit_assigned_engineer_for_derived_task(
                                task,
                                new_task,
                            )
                            _save_task(new_task)
                        if new_task:
                            _inherit_assigned_engineer_for_derived_task(
                                task,
                                new_task,
                            )
                        if new_task:
                            _append_mcp(
                                cell, "derive",
                                message[:80])
                            _append_task_msg(
                                task, "derive",
                                message, cell.name)
                            _record_history_msg(
                                cell, "derive",
                                message[:80])
                            review_verdict = (
                                _record_review_verdict_evidence(
                                    state,
                                    task,
                                    cell=cell,
                                    source_action="derive",
                                    message=derive_desc
                                    or message,
                                    derived_action=act_name,
                                    derived_task_id=new_task.id,
                                    pre_approved=bool(
                                        derive_pre_approved_by
                                    ),
                                    append_task_msg=(
                                        _append_task_msg
                                    ),
                                    record_history_msg=(
                                        _record_history_msg
                                    ),
                                )
                            )
                            _save_task(task)
                            state._emit_agent(cell)
                            if review_verdict:
                                _panel_event(
                                    "review_verdict",
                                    cell.id, cell.name,
                                    cell.group,
                                    _review_event_message(
                                        review_verdict
                                    ),
                                    task_id=task.id)
                            if not reused_existing_task:
                                _panel_event(
                                    "task_derived",
                                    cell.id, cell.name,
                                    cell.group,
                                    message[:80],
                                    task_id=new_task.id)
                            elif (
                                new_task.agent_id
                                and getattr(new_task, "lane", "") == "In Progress"
                            ):
                                result = {
                                    "type": "ok",
                                    "task_id": new_task.id,
                                    "agent_id": new_task.agent_id,
                                }
                            elif (
                                new_task.agent_id
                                and state.agent_is_busy(new_task.agent_id)
                            ):
                                result = {
                                    "type": "queued",
                                    "task_id": new_task.id,
                                    "agent_id": new_task.agent_id,
                                }
                            # Determine dispatch target
                            # Enforce transition's declared
                            # target — ignore --self/--agent
                            # if the transition specifies a
                            # different target
                            tr_target = ""
                            if cur_transitions and act_name:
                                for tr in cur_transitions:
                                    if isinstance(tr, dict) \
                                            and tr.get(
                                                "action"
                                            ) == act_name:
                                        tr_target = tr.get(
                                            "target", "")
                                        break
                            if tr_target == "self":
                                reuse_self = True
                                target_agent = ""
                                reviewer_target_source = "transition_self"
                            elif tr_target == "parent":
                                reuse_self = False
                                reviewer_target_source = "transition_parent"
                                pt = state.board_tasks.get(
                                    derive_parent_task_id) \
                                    if derive_parent_task_id \
                                    else None
                                if pt and pt.agent_id:
                                    a = state.agents.get(
                                        pt.agent_id)
                                    if a:
                                        target_agent = \
                                            a.slug or a.name
                            elif tr_target == "root":
                                reuse_self = False
                                reviewer_target_source = "transition_root"
                                rid = task.pipeline_root_id \
                                    or task.id
                                rt = state.board_tasks.get(
                                    rid)
                                if rt and rt.agent_id:
                                    a = state.agents.get(
                                        rt.agent_id)
                                    if a:
                                        target_agent = \
                                            a.slug or a.name
                            elif tr_target == "" \
                                    and cur_transitions \
                                    and not is_auto_review_gate:
                                # No explicit target declared.
                                # Reuse an ancestor thread only
                                # when this derive is clearly
                                # returning to a prior action
                                # stage (for example fix ->
                                # re-review). Otherwise keep the
                                # normal fresh-agent behavior.
                                reuse_self = False
                                if not target_agent:
                                    ancestor_agent = \
                                        _nearest_ancestor_agent_for_action_stage(
                                            state,
                                            task,
                                            act_name,
                                        )
                                    if ancestor_agent:
                                        target_agent = (
                                            ancestor_agent.slug
                                            or ancestor_agent.name
                                        )
                                        reviewer_target_source = (
                                            "automatic_ancestor_stage"
                                        )
                                    else:
                                        target_agent = ""
                            target_id = None
                            if reuse_self:
                                target_id = cell.id
                            elif target_agent:
                                target_id = \
                                    _resolve_agent_id(
                                        state,
                                        target_agent)
                                if not target_id:
                                    result = {
                                        "type": "error",
                                        "message":
                                            "Agent not "
                                            "found: "
                                            + target_agent
                                    }
                            elif reused_existing_task and new_task.agent_id:
                                target_id = new_task.agent_id

                            if (
                                    target_id
                                    and str(act_name or "").strip().lower()
                                    == _REVIEW_GATE_ACTION
                            ):
                                prior_review_task_ids = (
                                    _prior_review_task_ids_for_agent(
                                        state,
                                        task,
                                        target_id,
                                    )
                                )
                                if prior_review_task_ids:
                                    reviewer_reuse = (
                                        _reviewer_reuse_assignment(
                                            reviewer_id=target_id,
                                            prior_review_task_ids=(
                                                prior_review_task_ids
                                            ),
                                            selection_source=(
                                                reviewer_target_source
                                            ),
                                        )
                                    )
                                    _apply_reviewer_reuse_assignment(
                                        new_task,
                                        reviewer_reuse,
                                        cell.name,
                                    )
                                    _save_task(new_task)

                            if result and \
                                    result.get("type") \
                                    == "error":
                                pass  # skip dispatch
                            elif result and reused_existing_task:
                                pass
                            elif target_id == cell.id:
                                # Self-dispatch through the
                                # normal delayed same-agent
                                # prompt path.
                                dispatch_data = {
                                    "cmd": "dispatch_task",
                                    "id": new_task.id,
                                    "agent_id": cell.id,
                                    "_self_dispatch": True,
                                }
                                await state.broadcast()
                                dr = await handle_command(
                                    dispatch_data)
                                if dr and dr.get("type") \
                                        == "error":
                                    result = dr
                                else:
                                    result = {
                                        "type": "ok",
                                        "task_id":
                                            new_task.id,
                                        "agent_id":
                                            cell.id,
                                    }
                            elif target_id:
                                # Dispatch to different
                                # existing agent
                                dispatch_data = {
                                    "cmd": "dispatch_task",
                                    "id": new_task.id,
                                    "agent_id": target_id,
                                }
                                # The derived task reviews the caller's
                                # current worktree, not an idle reused
                                # reviewer's predecessor worktree.  Existing
                                # agents receive this handoff in
                                # dispatch_task before their prompt.
                                if cell.worktree_path:
                                    dispatch_data[
                                        "inherit_worktree"
                                        "_from"
                                    ] = cell.id
                                if cell.worktree_path:
                                    dispatch_data[
                                        "handoff_worktree_from"
                                    ] = cell.id
                                elif cell.worktree_branch:
                                    dispatch_data[
                                        "handoff_worktree_from"
                                    ] = cell.id
                                await state.broadcast()
                                dr = \
                                    await handle_command(
                                        dispatch_data)
                                if not _derive_handoff_accepted(dr):
                                    result = dr or {
                                        "type": "error",
                                        "message":
                                            "Derived task dispatch failed",
                                    }
                                else:
                                    if cell.current_task_id == task.id:
                                        cell.current_task_id = ""
                                    state._emit_agent(cell)
                                    state._db_save_agent(cell)
                                    result = {
                                        "type": (
                                            (dr or {}).get("type")
                                            or "ok"
                                        ),
                                        "task_id":
                                            new_task.id,
                                        "agent_id":
                                            (
                                                (dr or {}).get(
                                                    "agent_id"
                                                )
                                                or target_id
                                            )}
                            else:
                                # Default: new agent
                                dispatch_data = {
                                    "cmd":
                                        "dispatch_task",
                                    "id": new_task.id,
                                    "create_agent": True,
                                }
                                owner_engineer_id = \
                                    _ownership_engineer_id_for_dispatch_source(
                                        cell
                                    )
                                if owner_engineer_id:
                                    dispatch_data[
                                        "_created_by_engineer_id"
                                    ] = owner_engineer_id
                                # Worktree inheritance is resolved
                                # by dispatch_task from the derived
                                # task's structural parent.  Do not
                                # force the caller's branch here:
                                # review-derived fixes must skip
                                # the reviewer and land on the
                                # implementer's branch.
                                if cell.worktree_path \
                                        and derive_parent_task_id == task.id:
                                    dispatch_data[
                                        "inherit_worktree"
                                        "_from"
                                    ] = cell.id
                                if cell.worktree_path:
                                    dispatch_data[
                                        "handoff_worktree_from"
                                    ] = cell.id
                                if cell.session_id:
                                    dispatch_data[
                                        "target_session_id"
                                    ] = cell.session_id
                                if cell.window_id:
                                    dispatch_data[
                                        "target_window_id"
                                    ] = cell.window_id
                                await state.broadcast()
                                dr = \
                                    await handle_command(
                                        dispatch_data)
                                if not _derive_handoff_accepted(dr):
                                    result = dr or {
                                        "type": "error",
                                        "message":
                                            "Derived task dispatch failed",
                                    }
                                else:
                                    if cell.current_task_id == task.id:
                                        cell.current_task_id = ""
                                    state._emit_agent(cell)
                                    state._db_save_agent(cell)
                                    agent_id_result = (
                                        (dr or {}).get("agent_id")
                                        or new_task.agent_id
                                    )
                                    result = {
                                        "type": (
                                            (dr or {}).get("type")
                                            or "ok"
                                        ),
                                        "task_id":
                                            new_task.id,
                                        "agent_id":
                                            agent_id_result}
                            owner_engineer_id = (
                                _ownership_engineer_id_for_dispatch_source(
                                    cell
                                )
                                or str(
                                    getattr(
                                        task,
                                        "assigned_engineer_id",
                                        "",
                                    )
                                    or ""
                                ).strip()
                                or str(
                                    getattr(
                                        new_task,
                                        "assigned_engineer_id",
                                        "",
                                    )
                                    or ""
                                ).strip()
                            )
                            if reviewer_reuse and isinstance(result, dict):
                                result["reviewer_reuse"] = reviewer_reuse
                            _record_derive_dispatch_shape_metric(
                                state,
                                engineer_id=owner_engineer_id,
                                group=grp,
                                result=result,
                                new_task=new_task,
                                derive_parent_task_id=(
                                    derive_parent_task_id
                                ),
                                action_name=act_name,
                                target_id=target_id or "",
                                target_agent=target_agent,
                                reuse_self=bool(reuse_self),
                                transition_target=tr_target,
                                reused_existing_task=bool(
                                    reused_existing_task
                                ),
                            )
                        else:
                            result = {
                                "type": "error",
                                "message":
                                    "Failed to create "
                                    "derived task"}

        elif action == "ask":
            # Create a derived task in Backlog for human
            if not task:
                result = {"type": "error",
                          "message":
                              "No linked task to derive from"}
            elif not message:
                result = {"type": "error",
                          "message":
                              "Ask requires a question"}
            else:
                ask_targets_user = ask_recipient_is_user(
                    state, cell)
                # Keep parent in In Progress with
                # "Awaiting Input" status
                cell.activity = ""
                cell.activity_detail = ""
                cell.needs_attention = ask_targets_user
                cell.error_message = ""
                _append_mcp(cell, "ask", message)
                _append_task_msg(task, "ask",
                                 message, cell.name)
                _record_history_msg(cell, "ask", message)
                state._emit_agent(cell)
                task.status = "Awaiting Input"
                _save_task(task)
                # Propagate status to root
                root_id_s = task.pipeline_root_id \
                    or task.id
                if root_id_s != task.id:
                    root_t = state.board_tasks.get(
                        root_id_s)
                    if root_t:
                        root_t.status = "Awaiting Input"
                        _save_task(root_t)
                # Create HITL task in Backlog
                grp = task.group
                root_id = task.pipeline_root_id \
                    or task.id
                ask_desc = data.get(
                    "description", "")
                ask_assigned_engineer_id = ""
                if str(getattr(cell, "kind", "") or "") == "worker":
                    ask_assigned_engineer_id = (
                        _ownership_engineer_id_for_dispatch_source(cell)
                        or str(getattr(cell, "owner_engineer_id", "") or "")
                    )
                new_task = state.board_add_task(
                    task=message,
                    group=grp,
                    lane="Backlog",
                    labels=ask_task_labels_for_owner_recipient(
                        state,
                        cell,
                        ["torque:human", "torque:derived"],
                    ),
                    parent_task_id=task.id,
                    pipeline_depth=
                        task.pipeline_depth + 1,
                    pipeline_root_id=root_id,
                    description=ask_desc,
                    reply_agent_id=cell.id,
                    # A worker-level ask is owned by the Engineer that owns
                    # the asking worker.  This is deliberately task
                    # ownership, not a new Engineer capability: it lets the
                    # existing self-scoped ask-answer tool authorize the
                    # named recipient instead of leaving the row only
                    # group-visible to the board.
                    assigned_engineer_id=ask_assigned_engineer_id,
                )
                if new_task:
                    result = {
                        "type": "ok",
                        "task_id": new_task.id}
                    save_direct_ask_mirror(
                        state,
                        cell,
                        message,
                        source_task_id=new_task.id,
                    )
                    _panel_event(
                        "ask_created", cell.id,
                        cell.name, cell.group,
                        message,
                        task_id=new_task.id)
                else:
                    result = {
                        "type": "error",
                        "message":
                            "Failed to create ask task"}

        elif action == "name":
            if not message:
                result = {"type": "error",
                          "message":
                              "Name is required"}
            else:
                old_name = cell.name
                _append_mcp(cell, "name", message)
                _append_task_msg(task, "name",
                                 message, cell.name)
                _record_history_msg(
                    cell, "name", message)
                if task:
                    _save_task(task)
                state.update_agent(cell.id,
                                   name=message)
                state.history_update_agent(
                    cell, name=message,
                    slug=cell.slug)
                if cell.session_id:
                    await bridge.update_session(
                        cell, old_name)
                _panel_event(
                    "agent_renamed", cell.id,
                    cell.name, cell.group,
                    f"{old_name} \u2192 {cell.name}")
                state.recompute_task_health()
                result = {"type": "ok",
                          "slug": cell.slug}

        elif action == "reply":
            result = _handle_engineer_reply(
                state,
                cell,
                message=message,
                task_id=task_id,
                panel_event=_panel_event,
            )

        else:
            result = {"type": "error",
                      "message":
                          f"Unknown ai action: {action}"}

    return result
