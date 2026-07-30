"""Review policy, verdict evidence, deliverable, and mandatory-gate helpers."""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime, timezone

from .actions import ActionManager, DEFAULT_REVIEW_REQUIRED_ABOVE_LOC
from .config import log
from .identity import agent_kind_for_identity
from .server_dispatch import _cells_share_worktree_context
from .server_evidence import (
    _completion_evidence_text,
    _merge_completion_evidence,
    _save_completion_evidence_task,
)
from .server_prompts import deliverable_word
from .services.worktrees import _emit_workflow_breach_event
from .state import (
    MatrixState,
    normalize_architect_review_gate_thresholds,
    task_counts_as_done,
    task_is_closed,
)
from .worktree import WorktreeManager
from .worktree_boundaries import branch_boundary_tasks
from .worktree_streams import compute_worktree_stream


def _ai_derive_parent_task(state: MatrixState, task):
    """Return the structural parent for a newly derived ``torque ai`` task.

    Review tasks hand fixes back to their implementation parent.  Making the
    fix task a child of the implementer task keeps dispatch worktree
    inheritance on the implementer's branch instead of walking through the
    reviewer task/agent.
    """
    if not state or not task:
        return None
    if _is_feature_review_task(task):
        parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
        parent = state.board_tasks.get(parent_id) if parent_id else None
        if parent:
            return parent
    return task


def _resolve_inherited_worktree_source(
    state: MatrixState,
    task,
    inherit_from: str = "",
):
    """Resolve the agent whose worktree should seed a new task dispatch."""
    if not state or not task:
        return None
    inherit_from = str(inherit_from or "").strip()
    if inherit_from:
        src = state.agents.get(inherit_from)
        if src and getattr(src, "worktree_path", ""):
            return src
        return None

    # HITL dispatch: walk parent chain to find the worktree before launching
    # the session, so derived reviewers/fixes do not briefly create and run
    # inside a throwaway branch.
    parent_task_id = str(getattr(task, "parent_task_id", "") or "").strip()
    seen = set()
    while parent_task_id and parent_task_id not in seen:
        seen.add(parent_task_id)
        parent_task = state.board_tasks.get(parent_task_id)
        if not parent_task:
            break
        parent_agent_id = str(
            getattr(parent_task, "agent_id", "") or ""
        ).strip()
        if parent_agent_id:
            parent_agent = state.agents.get(parent_agent_id)
            if parent_agent and getattr(parent_agent, "worktree_path", ""):
                return parent_agent
        parent_task_id = str(
            getattr(parent_task, "parent_task_id", "") or ""
        ).strip()
    return None


def _agent_can_receive_dispatch(cell) -> bool:
    return bool(
        cell
        and cell.cell_type == "agent"
        and cell.session_id
        and (cell.status or "") not in {"stopped", "error"}
    )


def _promote_task_for_active_report(state: MatrixState, cell, task) -> None:
    """Normalize a task into the dispatch lane once work has clearly started."""
    if not cell or not task:
        return
    if task.agent_id and task.agent_id != cell.id:
        return
    fields = {}
    if not task.agent_id:
        fields["agent_id"] = cell.id
    if task.lane in {"Backlog", "To Do"}:
        fields["lane"] = (
            state.get_group_settings(task.group).dispatch_lane
            or "In Progress"
        )
    if fields:
        state.board_update_task(task.id, **fields)
    if cell.current_task_id != task.id:
        cell.current_task_id = task.id
        state._emit_agent(cell)
        state._db_save_agent(cell)


async def _worktree_branch_has_commits_ahead(cell, worktree_mgr) -> bool:
    """Return whether ``cell``'s worktree branch is ahead of its base branch."""
    if not cell:
        return False
    try:
        if int(getattr(cell, "worktree_ahead", 0) or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass

    if not (
        getattr(cell, "worktree_path", "")
        and getattr(cell, "worktree_base_branch", "")
    ):
        return False
    ahead_behind = getattr(worktree_mgr, "_ahead_behind", None)
    if not callable(ahead_behind):
        return False
    try:
        result = ahead_behind(cell)
        if asyncio.iscoroutine(result):
            result = await result
        ahead = result[0] if result else 0
        return int(ahead or 0) > 0
    except Exception:
        log.exception(
            "Failed to probe worktree ahead state for '%s'",
            getattr(cell, "name", "") or getattr(cell, "id", ""),
        )
        return False


def _stream_review_derive_parent_task(state: MatrixState, stream: dict):
    """Return the implementation parent implied by a computed worktree stream."""
    if not state or not stream:
        return None
    candidate_ids = []
    for key in ("foreground_task_id", "latest_boundary_task_id"):
        task_id = str(stream.get(key, "") or "").strip()
        if task_id:
            candidate_ids.append(task_id)
    for task_id in reversed(stream.get("product_task_ids", []) or []):
        task_id = str(task_id or "").strip()
        if task_id:
            candidate_ids.append(task_id)

    seen = set()
    for task_id in candidate_ids:
        if task_id in seen:
            continue
        seen.add(task_id)
        task = state.board_tasks.get(task_id)
        if not task:
            continue
        if _looks_like_review_task(task):
            continue
        return task
    return None


def _stream_has_open_feature_review_boundary(
    state: MatrixState,
    *,
    repo_root: str,
    branch: str,
) -> bool:
    """Return whether a feature/review boundary is already open on a stream."""
    if not state or not repo_root or not branch:
        return False
    for task in branch_boundary_tasks(
        state.board_tasks.values(),
        repo_root=repo_root,
        branch=branch,
        statuses={"open"},
    ):
        if _is_feature_review_task(task):
            return True
    return False


async def _resolve_feature_review_derive_stream_backstop_task(
    state: MatrixState,
    cell,
    worktree_mgr,
):
    """Resolve a feature/review derive parent from worktree stream state.

    This intentionally does not rehydrate ``cell.current_task_id``.  It is only
    a narrow backstop for resumed/non-linked workers that still have branch work
    to review and no review already in flight for the same worktree stream.
    """
    if not state or not cell:
        return None
    if str(getattr(cell, "current_task_id", "") or "").strip():
        return None

    repo_root = str(
        getattr(cell, "worktree_repo_root", "")
        or getattr(cell, "git_root", "")
        or ""
    ).strip()
    branch = str(getattr(cell, "worktree_branch", "") or "").strip()
    if not repo_root or not branch:
        return None
    if not await _worktree_branch_has_commits_ahead(cell, worktree_mgr):
        return None

    try:
        stream = compute_worktree_stream(
            state,
            repo_root=repo_root,
            branch=branch,
            group=getattr(cell, "group", "") or "",
            stream_agent_ids={getattr(cell, "id", "")},
            branch_exists_cache={
                (os.path.realpath(os.path.expanduser(repo_root)), branch):
                True
            },
        ) or {}
    except Exception:
        log.exception(
            "Failed to compute worktree stream for feature/review derive "
            "backstop on branch '%s'",
            branch,
        )
        return None

    if str(stream.get("active_review_task_id", "") or "").strip():
        return None
    if _stream_has_open_feature_review_boundary(
        state,
        repo_root=repo_root,
        branch=branch,
    ):
        return None
    return _stream_review_derive_parent_task(state, stream)


def _reject_completion_with_open_descendants(state: MatrixState, task,
                                             action_name: str) -> dict | None:
    if not task:
        return None
    if not state.task_has_unresolved_descendants(task.id):
        return None
    return {
        "type": "error",
        "message":
            f"Cannot mark task {action_name}: "
            "derived follow-up work is still unresolved",
        "task_id": task.id,
    }
def _nearest_ancestor_agent_for_action_stage(state: MatrixState, task,
                                             action_name: str):
    """Find the closest ancestor already associated with ``action_name``."""
    if not task or not action_name:
        return None
    ancestor_id = task.parent_task_id
    while ancestor_id:
        ancestor = state.board_tasks.get(ancestor_id)
        if not ancestor:
            break
        if ancestor.action_name == action_name and ancestor.agent_id:
            agent = state.agents.get(ancestor.agent_id)
            if _agent_can_receive_dispatch(agent):
                return agent
        ancestor_id = ancestor.parent_task_id
    return None


def _prior_live_reviewer_agent_for_chain(state: MatrixState, task):
    """Find the most recent live feature/review agent in ``task``'s chain."""
    if not state or not task:
        return None
    for prior in reversed(state.board_get_chain(task.id)):
        if prior.id == task.id:
            continue
        action_name = str(
            getattr(prior, "action_name", "") or ""
        ).strip().lower()
        if action_name != _REVIEW_GATE_ACTION:
            continue
        agent_id = str(getattr(prior, "agent_id", "") or "").strip()
        if not agent_id:
            continue
        agent = state.agents.get(agent_id)
        if _agent_can_receive_dispatch(agent):
            return agent
    return None


def _looks_like_review_task(task) -> bool:
    if not task:
        return False
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    if "review" in action_name:
        return True
    status = str(getattr(task, "status", "") or "").strip().lower()
    if status == "on review":
        return True
    text = " ".join(
        part.strip().lower()
        for part in (
            str(getattr(task, "task", "") or ""),
            str(getattr(task, "description", "") or ""),
            action_name,
            status,
        )
        if part and part.strip()
    )
    return "review" in text or "re-review" in text


def _is_feature_review_task(task) -> bool:
    if not task:
        return False
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    return action_name == "feature/review"


def _task_ancestry_has_agent(state: MatrixState, task,
                             agent_id: str) -> bool:
    """Return whether ``task`` or any ancestor is assigned to ``agent_id``."""
    agent_id = str(agent_id or "").strip()
    if not state or not task or not agent_id:
        return False
    seen = set()
    cursor = task
    while cursor and getattr(cursor, "id", "") not in seen:
        seen.add(getattr(cursor, "id", ""))
        if str(getattr(cursor, "agent_id", "") or "").strip() == agent_id:
            return True
        parent_id = str(getattr(cursor, "parent_task_id", "") or "").strip()
        if not parent_id:
            break
        cursor = state.board_tasks.get(parent_id)
    return False


def _active_shared_worktree_review_for_cell(state: MatrixState, cell):
    """Return an active reviewer task that owns ``cell``'s shared worktree.

    TORQUE:88 intentionally launches feature/review workers in the
    implementer's worktree. During that review window, the implementer is a
    suspended ancestor in the task graph, so Torque-originated checkpoint writes
    from that implementer must fail closed while the reviewer owns the mutable
    branch.
    """
    if (
        not state
        or not cell
        or not (cell.worktree_path or cell.worktree_branch)
    ):
        return None
    cell_id = str(getattr(cell, "id", "") or "").strip()
    if not cell_id:
        return None

    for task in state.board_tasks.values():
        if not _is_feature_review_task(task):
            continue
        if task_is_closed(task):
            continue
        reviewer_id = str(getattr(task, "agent_id", "") or "").strip()
        if not reviewer_id or reviewer_id == cell_id:
            continue
        # When a review derives blocker fixes back to the implementer, the
        # review task remains open/status=Fixing but no longer owns the
        # foreground mutable branch; the descendant fix task does.
        if not state.task_occupies_execution_slot(
                task,
                agent_id=reviewer_id):
            continue
        reviewer = state.agents.get(reviewer_id)
        if not _cells_share_worktree_context(cell, reviewer):
            continue
        if _task_ancestry_has_agent(state, task, cell_id):
            return task
    return None


def _shared_review_checkpoint_block_reason(state: MatrixState, cell) -> str:
    """Explain why ``cell`` cannot checkpoint during an active review."""
    review_task = _active_shared_worktree_review_for_cell(state, cell)
    if not review_task:
        return ""
    review_label = getattr(review_task, "id", "") or "active review"
    cell_label = getattr(cell, "name", "") or getattr(cell, "id", "")
    return (
        f"Cannot checkpoint '{cell_label}' while "
        f"feature/review task {review_label} is active on the shared "
        "worktree. Checkpoint the reviewer worker instead, or wait for "
        "the review to finish."
    )


def _normalized_review_verdict_line(line: str) -> str:
    text = str(line or "").strip()
    while text[:1] in {"#", "-", "*"}:
        text = text[1:].strip()
    for token in ("**", "__"):
        text = text.replace(token, "")
    text = text.strip()
    lower = text.lower()
    for label in ("final review verdict", "review verdict", "verdict"):
        if lower.startswith(label):
            rest = text[len(label):].lstrip()
            if rest[:1] in {":", "-", "—", "–"}:
                text = rest[1:].strip()
            else:
                text = rest.strip()
            break
    return text.strip()


_INLINE_FINAL_REVIEW_VERDICT_RE = re.compile(
    r"(?:^|(?<=[.!?])\s+)final\s+review\s+verdict\s*"
    r"(?:[:—–-])\s*(?P<verdict>"
    r"ship(?:\s+it|\s+with\s+fixes)?|needs\s+(?:rework|changes)|"
    r"blocker|revert)\.?$",
    re.IGNORECASE,
)


def _inline_final_review_verdict_text(line: str) -> str:
    """Return a conservatively anchored inline final-verdict value.

    A bare ``Final review verdict`` label may appear after a completed prose
    sentence in a reviewer report. Deliberately require that sentence
    boundary, the complete label, and a terminal instructed verdict rather
    than searching arbitrary prose: quoted labels and examples remain
    non-authoritative.
    """
    raw_line = str(line or "")
    # A Markdown blockquote is illustrative/quoted material even when its
    # contents include a sentence boundary before an otherwise-valid label.
    # Reject it before the anchored search can scan inside the quoted prose.
    if re.match(r"^\s{0,3}>", raw_line):
        return ""
    match = _INLINE_FINAL_REVIEW_VERDICT_RE.search(raw_line.strip())
    return match.group("verdict").strip() if match else ""


def _review_verdict_from_text(text: str) -> str:
    """Classify one already-isolated candidate verdict value."""
    lower = str(text or "").lower().strip(" .")
    if lower.startswith("ship with fixes"):
        return "ship_with_fixes"
    if lower.startswith(("needs rework", "needs changes", "blocker")):
        return "needs_rework"
    if lower == "revert" or (
            lower.startswith("revert")
            and len(lower) > len("revert")
            and lower[len("revert")] in {
                " ", ":", "-", "—", "–", ",", ";",
            }):
        return "needs_rework"
    if lower == "ship" or lower == "ship it":
        return "ship"
    if lower.startswith("ship") and len(lower) > 4:
        next_char = lower[4]
        if next_char in {" ", ".", ":", "-", "—", "–", ",", ";"}:
            return "ship"
    return ""


def _review_verdict_from_message(message: str) -> str:
    """Return ``ship`` or a non-ship verdict parsed from a review message.

    This is intentionally a lightweight free-form parser: explicit verdict
    lines may have markdown/bullet prefixes and varied casing, but paraphrases
    such as "looks good" or "approved" fail closed so reviewer cleanup does
    not fire from an ambiguous message. An inline final label is allowed only
    after a sentence boundary, never by arbitrary substring matching.
    """
    for line in reversed(str(message or "").splitlines()):
        text = _normalized_review_verdict_line(line)
        if not text:
            continue
        verdict = _review_verdict_from_text(text)
        if verdict:
            return verdict
        verdict = _review_verdict_from_text(
            _inline_final_review_verdict_text(line))
        if verdict:
            return verdict
    return ""


def _normalize_review_followup_classification(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("_", "-")
    text = re.sub(r"\s+", " ", text)
    if (
            "future-context" in text
            or "future context" in text
            or text in {"future", "future-only", "future only"}):
        return "future_context"
    if (
            "non-blocking" in text
            or "non blocking" in text
            or "optional" in text
            or "follow-up" in text
            or "follow up" in text):
        return "non_blocking_now"
    if "blocking" in text or text in {"block", "blocker", "blockers"}:
        return "blocking"
    if text in {"none", "no", "no follow-ups", "no follow ups", "n/a", "na"}:
        return "none"
    return ""


def _review_followup_classification_from_message(message: str) -> str:
    """Best-effort parse of a review's structured follow-up class."""
    lines = str(message or "").splitlines()
    for line in reversed(lines):
        text = _normalized_review_verdict_line(line)
        if not text:
            continue
        lower = text.lower()
        for label in (
                "follow-up classification",
                "follow up classification",
                "follow-up class",
                "follow up class",
                "follow-up",
                "follow up"):
            if not lower.startswith(label):
                continue
            value = text[len(label):].strip()
            if value[:1] in {":", "-", "—", "–"}:
                value = value[1:].strip()
            normalized = _normalize_review_followup_classification(value)
            if normalized:
                return normalized

    text = str(message or "")
    lower = text.lower()
    if "future-context only" in lower or "future context only" in lower:
        return "future_context"
    if (
            re.search(r"follow[- ]up suggestions\W+(none|no\b)", lower)
            or re.search(r"follow[- ]ups?\W+(none|no\b)", lower)):
        return "none"
    if (
            "follow-up suggestions" in lower
            or "follow up suggestions" in lower):
        return "non_blocking_now"
    return ""


def _task_review_evidence(task) -> dict:
    evidence = getattr(task, "completion_evidence", {}) or {}
    if not isinstance(evidence, dict):
        return {}
    review = evidence.get("review", {}) or {}
    return review if isinstance(review, dict) else {}


def _review_event_message(review: dict) -> str:
    verdict = str((review or {}).get("verdict", "") or "unknown").strip()
    followup = str(
        (review or {}).get("follow_up_classification", "") or ""
    ).strip()
    parts = [f"Final review verdict: {verdict}"]
    if followup:
        parts.append(f"follow-ups={followup}")
    derived = str((review or {}).get("derived_task_id", "") or "").strip()
    if derived:
        parts.append(f"derived_task={derived}")
    return "; ".join(parts)


def _build_review_verdict_payload(*, task, cell=None, source_action: str,
                                  message: str = "",
                                  derived_action: str = "",
                                  derived_task_id: str = "",
                                  pre_approved: bool = False,
                                  timestamp: str = "") -> dict:
    """Return a structured final verdict payload for a feature/review task."""
    if not task or not _is_feature_review_task(task):
        return {}

    source_action = _completion_evidence_text(source_action, limit=80)
    full_message = str(message or "").strip()
    # The summary is bounded so completion evidence, board snapshots, and
    # websocket payloads cannot grow without limit. Parse the complete report
    # first: a final verdict commonly follows a detailed review body.
    message = _completion_evidence_text(full_message, limit=2000)
    derived_action = _completion_evidence_text(derived_action, limit=120)
    verdict = ""
    followup = _review_followup_classification_from_message(full_message)
    parsed = _review_verdict_from_message(full_message)

    if source_action == "derive":
        if pre_approved or derived_action.endswith("preapproved"):
            verdict = "needs_followup"
            followup = followup or "non_blocking_now"
        elif derived_action in {"feature/implement", "feature/fix-review"}:
            verdict = "block"
            followup = "blocking"
    if not verdict:
        if parsed == "ship":
            verdict = "ship"
        elif parsed == "ship_with_fixes":
            verdict = "needs_followup"
            followup = followup or "non_blocking_now"
        elif parsed == "needs_rework":
            verdict = "block"
            followup = "blocking"
    if not verdict and source_action == "done":
        verdict = "unknown"
    if not verdict:
        return {}
    if not followup and verdict == "ship":
        followup = "none"
    elif not followup and verdict == "needs_followup":
        followup = "non_blocking_now"
    elif verdict == "block":
        followup = "blocking"

    recorded_at = timestamp or datetime.now(timezone.utc).isoformat()
    payload = {
        "verdict": verdict,
        "follow_up_classification": followup,
        "source_action": source_action,
        "summary": message,
        "recorded_at": recorded_at,
    }
    if len(full_message) > len(message):
        payload["summary_truncated"] = True
    if parsed:
        payload["parsed_verdict"] = parsed
    if cell:
        payload["agent_id"] = _completion_evidence_text(
            getattr(cell, "id", ""), limit=80)
        payload["agent_name"] = _completion_evidence_text(
            getattr(cell, "name", ""), limit=160)
    if derived_action:
        payload["derived_action"] = derived_action
    if derived_task_id:
        payload["derived_task_id"] = _completion_evidence_text(
            derived_task_id, limit=80)
    if pre_approved:
        payload["pre_approved_followup"] = True
    return payload


def _record_review_verdict_evidence(
        state: MatrixState,
        task,
        *,
        cell=None,
        source_action: str,
        message: str = "",
        derived_action: str = "",
        derived_task_id: str = "",
        pre_approved: bool = False,
        append_task_msg=None,
        record_history_msg=None,
        timestamp: str = "",
) -> dict:
    """Persist a structured final feature/review verdict if one is present."""
    if not state or not task:
        return {}
    # A final review is a factual statement by its reviewer.  Do not let a
    # repeated completion/derive report replace the first structured record;
    # an observed parser error has the explicit append-only amendment route.
    existing_review = _task_review_evidence(task)
    if str(existing_review.get("verdict", "") or "").strip():
        return existing_review
    review = _build_review_verdict_payload(
        task=task,
        cell=cell,
        source_action=source_action,
        message=message,
        derived_action=derived_action,
        derived_task_id=derived_task_id,
        pre_approved=pre_approved,
        timestamp=timestamp,
    )
    if not review:
        return {}

    actor_name = _completion_evidence_text(
        getattr(cell, "name", ""), limit=160) or "torque"
    update = {
        "sources": ["review"],
        "review": review,
        "updated_at": review["recorded_at"],
        "updated_by": actor_name,
    }
    task.completion_evidence = _merge_completion_evidence(
        getattr(task, "completion_evidence", {}) or {},
        update,
    )
    event_message = _review_event_message(review)
    if append_task_msg:
        append_task_msg(task, "review_verdict", event_message, actor_name)
    if record_history_msg and cell:
        record_history_msg(cell, "review_verdict", event_message)
    _save_completion_evidence_task(state, task)
    return review


def _review_verdict_amendment_verdict(review: dict) -> str:
    """Return the one valid correction, failing closed on stored corruption."""
    if not isinstance(review, dict):
        return ""
    if str(review.get("verdict", "") or "").strip() != "unknown":
        return ""
    reviewer_id = str(review.get("agent_id", "") or "").strip()
    if not reviewer_id:
        return ""
    amendments = review.get("amendments", []) if isinstance(review, dict) else []
    # The durable correction contract is deliberately one-shot. A Block
    # correction is terminal, and a corrupted/multi-row history must never
    # make the Ship gate permissive.
    if not isinstance(amendments, list) or len(amendments) != 1:
        return ""
    amendment = amendments[0]
    if not isinstance(amendment, dict):
        return ""
    if (
            str(amendment.get("original_verdict", "") or "").strip()
            != "unknown"
            or str(amendment.get("prior_verdict", "") or "").strip()
            != "unknown"
            or str(amendment.get("amended_by_id", "") or "").strip()
            != reviewer_id
    ):
        return ""
    if not str(amendment.get("amended_by_name", "") or "").strip():
        return ""
    if not str(amendment.get("reason", "") or "").strip():
        return ""
    amended_at = str(amendment.get("amended_at", "") or "").strip()
    if not amended_at:
        return ""
    try:
        datetime.fromisoformat(amended_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    verdict = str(amendment.get("corrected_verdict", "") or "").strip()
    return verdict if verdict in {"ship", "block", "needs_followup"} else ""


def _amend_review_verdict_evidence(
        state: MatrixState,
        task,
        *,
        cell,
        verdict: str,
        reason: str,
        timestamp: str = "",
        append_task_msg=None,
) -> tuple[dict, str]:
    """Append an attributable correction to an original ``unknown`` verdict.

    The parsed review object is intentionally immutable. Corrections retain it
    and append ordered facts, so the record is writable after completion or
    merge without becoming a silent overwrite.
    """
    if not state or not task or not _is_feature_review_task(task):
        return {}, "Task has no structured feature/review verdict to amend."
    evidence = getattr(task, "completion_evidence", {}) or {}
    if not isinstance(evidence, dict):
        return {}, "Task has no structured feature/review verdict to amend."
    original_review = evidence.get("review", {}) or {}
    if not isinstance(original_review, dict):
        return {}, "Task has no structured feature/review verdict to amend."
    original_verdict = str(original_review.get("verdict", "") or "").strip()
    original_reviewer_id = str(
        original_review.get("agent_id", "") or ""
    ).strip()
    caller_id = str(getattr(cell, "id", "") or "").strip()
    if not original_reviewer_id or original_reviewer_id != caller_id:
        return {}, (
            "Authorization denied: only the original reviewer may amend this "
            "recorded verdict. Use review_verdict_amend from the reviewer that "
            "recorded the verdict."
        )
    if original_verdict != "unknown":
        return {}, (
            "Review verdict amendments are supported only for an original "
            "structured unknown verdict; recorded ship, block, and "
            "needs_followup verdicts are immutable."
        )
    existing_amendments = original_review.get("amendments", [])
    if existing_amendments:
        return {}, (
            "Review verdict already has an append-only amendment and cannot "
            "be amended again; a corrected block is terminal."
        )
    if not isinstance(existing_amendments, list):
        return {}, (
            "Review verdict amendment history is malformed and cannot be "
            "amended."
        )
    corrected_verdict = str(verdict or "").strip().lower()
    if corrected_verdict not in {"ship", "block", "needs_followup"}:
        return {}, "verdict must be one of: ship, block, needs_followup"
    amendment_reason = _completion_evidence_text(reason, limit=2000)
    if not amendment_reason:
        return {}, "reason is required"

    prior_verdict = (
        _review_verdict_amendment_verdict(original_review) or original_verdict
    )
    amended_at = timestamp or datetime.now(timezone.utc).isoformat()
    amendment = {
        "original_verdict": original_verdict,
        "prior_verdict": prior_verdict,
        "corrected_verdict": corrected_verdict,
        "amended_by_id": caller_id,
        "amended_by_name": _completion_evidence_text(
            getattr(cell, "name", ""), limit=160
        ) or "reviewer",
        "amended_at": amended_at,
        "reason": amendment_reason,
    }
    review = dict(original_review)
    amendments = list(review.get("amendments", []) or [])
    amendments.append(amendment)
    review["amendments"] = amendments
    updated_evidence = dict(evidence)
    updated_evidence["review"] = review
    sources = list(updated_evidence.get("sources", []) or [])
    if "review" not in sources:
        sources.append("review")
    updated_evidence["sources"] = sources
    updated_evidence["updated_at"] = amended_at
    updated_evidence["updated_by"] = amendment["amended_by_name"]
    task.completion_evidence = updated_evidence
    event_message = (
        "Review verdict amendment: "
        f"{prior_verdict} -> {corrected_verdict}; original={original_verdict}"
    )
    if append_task_msg:
        append_task_msg(
            task, "review_verdict_amendment", event_message,
            amendment["amended_by_name"],
        )
    _save_completion_evidence_task(state, task)
    return amendment, ""


def _review_task_has_ship_verdict(task) -> bool:
    if not task:
        return False
    review = _task_review_evidence(task)
    verdict = str(review.get("verdict", "") or "").strip()
    amended_verdict = _review_verdict_amendment_verdict(review)
    if amended_verdict:
        return amended_verdict == "ship"
    if verdict:
        return verdict == "ship"
    for entry in reversed(getattr(task, "messages", []) or []):
        if str(entry.get("action", "") or "").lower() != "done":
            continue
        verdict = _review_verdict_from_message(entry.get("message", ""))
        if verdict:
            return verdict == "ship"
    status_verdict = _review_verdict_from_message(
        getattr(task, "status", "") or ""
    )
    if status_verdict:
        return status_verdict == "ship"
    return False


_REVIEW_GATE_ACTION = "feature/review"


def _coerce_action_bool(value) -> bool:
    """Return a conservative boolean for action YAML metadata."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _action_is_implementation_depth(act: dict | None) -> bool:
    """Return whether an action represents code-mutating implementation work."""
    if not isinstance(act, dict):
        return False
    if "implementation_depth" in act:
        return _coerce_action_bool(act.get("implementation_depth"))
    # Backward compatibility: pre-field actions that already opted into the
    # LOC review gate should keep their existing behavior until edited.
    return "review_required_above_loc" in act


def _review_gate_threshold_from_action(act: dict | None) -> int | None:
    """Return an implementation action's review-required LOC threshold."""
    if not _action_is_implementation_depth(act):
        return None
    if "review_required_above_loc" not in act:
        return DEFAULT_REVIEW_REQUIRED_ABOVE_LOC
    try:
        threshold = int(act.get("review_required_above_loc"))
    except (TypeError, ValueError):
        return None
    return threshold if threshold >= 0 else None


def _explicit_review_gate_threshold_from_action(act: dict | None) -> int | None:
    """Return only the explicitly configured action-level LOC threshold."""
    if not _action_is_implementation_depth(act):
        return None
    if "review_required_above_loc" not in act:
        return None
    try:
        threshold = int(act.get("review_required_above_loc"))
    except (TypeError, ValueError):
        return None
    return threshold if threshold >= 0 else None


def _review_gate_policy_from_loc_gate(loc_gate, *, source: str,
                                      action: str = "") -> dict | None:
    """Normalize a transition-local LOC gate block into a policy dict."""
    if not isinstance(loc_gate, dict):
        return None
    thresholds = normalize_architect_review_gate_thresholds(loc_gate)
    ship_direct_max = int(thresholds.get("ship_direct_max", 0) or 0)
    review_default_above = int(
        thresholds.get("review_default_above", DEFAULT_REVIEW_REQUIRED_ABOVE_LOC)
        or 0
    )
    return {
        "source": source,
        "action": action,
        "threshold": max(ship_direct_max, review_default_above),
        "ship_direct_max": ship_direct_max,
        "review_default_above": review_default_above,
        "self_review_bypass_allowed": bool(
            thresholds.get("self_review_bypass_allowed", False)
        ),
        "controls_self_review_bypass": True,
    }


def _review_gate_transition_policy(act: dict | None) -> dict | None:
    """Return the first feature/review transition-local LOC gate policy."""
    if not isinstance(act, dict):
        return None
    transitions = act.get("transitions") or []
    if not isinstance(transitions, list):
        return None
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        action = str(transition.get("action", "") or "").strip()
        if action.lower() != _REVIEW_GATE_ACTION:
            continue
        if "loc_gate" not in transition:
            continue
        policy = _review_gate_policy_from_loc_gate(
            transition.get("loc_gate"),
            source="transition",
            action=action,
        )
        if policy:
            return policy
    return None


def _review_gate_policy_from_action_threshold(
        threshold: int | None) -> dict | None:
    if threshold is None:
        return None
    return {
        "source": "action",
        "threshold": threshold,
        "controls_self_review_bypass": False,
    }


def _review_gate_task_chain(state: MatrixState, task) -> list:
    """Return the root→leaf-ish chain available for review-gate scoping."""
    if not state or not task:
        return []
    task_id = str(getattr(task, "id", "") or "").strip()
    if not task_id:
        return [task]
    try:
        chain = list(state.board_get_chain(task_id) or [])
    except Exception:
        chain = []
    if task not in chain:
        chain.append(task)
    return chain


def _review_gate_architect_id(state: MatrixState, task, cell=None) -> str:
    """Return the architect whose settings should shape this review gate."""
    for chain_task in _review_gate_task_chain(state, task):
        architect_id = str(
            getattr(chain_task, "created_by_architect_id", "") or ""
        ).strip()
        if architect_id:
            return architect_id

    for chain_task in _review_gate_task_chain(state, task):
        engineer_id = str(
            getattr(chain_task, "assigned_engineer_id", "") or ""
        ).strip()
        engineer = state.agents.get(engineer_id) if engineer_id else None
        architect_id = str(
            getattr(engineer, "hired_by_architect_id", "") or ""
        ).strip()
        if architect_id:
            return architect_id

    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip() or str(
        getattr(cell, "created_by_engineer_id", "") or ""
    ).strip()
    owner = state.agents.get(owner_id) if owner_id else None
    return str(getattr(owner, "hired_by_architect_id", "") or "").strip()


def _review_gate_architect_policy(state: MatrixState, task, cell=None) -> dict | None:
    """Return architect-configured review-gate thresholds for scoped work."""
    architect_id = _review_gate_architect_id(state, task, cell)
    if not architect_id:
        return None
    architect = state.agents.get(architect_id)
    group = str(
        getattr(architect, "group", "") or getattr(task, "group", "") or ""
    ).strip()
    if not group or not hasattr(state, "get_architect_settings"):
        return None
    settings = state.get_architect_settings(group)
    thresholds = normalize_architect_review_gate_thresholds(
        getattr(settings, "architect_review_gate_thresholds", {})
    )
    ship_direct_max = int(thresholds.get("ship_direct_max", 0) or 0)
    review_default_above = int(
        thresholds.get("review_default_above", DEFAULT_REVIEW_REQUIRED_ABOVE_LOC)
        or 0
    )
    return {
        "source": "architect",
        "architect_id": architect_id,
        "threshold": max(ship_direct_max, review_default_above),
        "ship_direct_max": ship_direct_max,
        "review_default_above": review_default_above,
        "self_review_bypass_allowed": bool(
            thresholds.get("self_review_bypass_allowed", False)
        ),
        "controls_self_review_bypass": True,
    }


def _chain_has_shipped_review(state: MatrixState, task) -> bool:
    """Return whether this task chain already has a closed Ship review."""
    if not state or not task:
        return False
    for chain_task in state.board_get_chain(task.id):
        if not task_counts_as_done(chain_task):
            continue
        if not _is_feature_review_task(chain_task):
            continue
        if _review_task_has_ship_verdict(chain_task):
            return True
    return False


def _feature_review_transition_is_mandatory(transition) -> bool:
    if not isinstance(transition, dict):
        return str(transition or "").strip().lower() == _REVIEW_GATE_ACTION
    action = str(transition.get("action", "") or "").strip().lower()
    if action != _REVIEW_GATE_ACTION:
        return False
    # oneshot/* actions use a feature/review transition for the optional
    # diff-size review gate.  That is not the mandatory feature pipeline
    # closeout contract this guard enforces.
    when = str(transition.get("when", "") or "").strip().lower()
    if "review gate threshold" in when or "diff exceeded" in when:
        return False
    return True


def _action_requires_mandatory_feature_review(
        action_mgr: ActionManager | None,
        action_name: str,
        base_dir: str = "") -> bool:
    action_name = str(action_name or "").strip()
    if not action_name:
        return False
    if action_name.lower() == "feature/implement":
        return True
    if not action_mgr:
        return False
    try:
        transitions = action_mgr.get_transitions(action_name, base_dir) or []
    except Exception:
        return False
    return any(
        _feature_review_transition_is_mandatory(transition)
        for transition in transitions
    )


def _task_is_pipeline_descendant(
        state: MatrixState,
        task,
        candidate) -> bool:
    if not state or not task or not candidate:
        return False
    task_id = str(getattr(task, "id", "") or "").strip()
    candidate_id = str(getattr(candidate, "id", "") or "").strip()
    if not task_id or not candidate_id or candidate_id == task_id:
        return False

    task_root_id = str(
        getattr(task, "pipeline_root_id", "") or task_id
    ).strip()
    candidate_root_id = str(
        getattr(candidate, "pipeline_root_id", "") or candidate_id
    ).strip()
    if task_root_id and candidate_root_id and task_root_id != candidate_root_id:
        return False

    if not str(getattr(task, "parent_task_id", "") or "").strip():
        return candidate_root_id == task_id

    seen = set()
    parent_id = str(getattr(candidate, "parent_task_id", "") or "").strip()
    while parent_id and parent_id not in seen:
        if parent_id == task_id:
            return True
        seen.add(parent_id)
        parent = state.board_tasks.get(parent_id)
        if not parent:
            break
        parent_id = str(getattr(parent, "parent_task_id", "") or "").strip()
    return False


def _task_has_shipped_review_descendant(state: MatrixState, task) -> bool:
    """Return whether ``task`` has a closed descendant Ship review."""
    if not state or not task:
        return False
    for chain_task in state.board_get_chain(task.id):
        if not _task_is_pipeline_descendant(state, task, chain_task):
            continue
        if not task_counts_as_done(chain_task):
            continue
        if not _is_feature_review_task(chain_task):
            continue
        if _review_task_has_ship_verdict(chain_task):
            return True
    return False


def _reviewer_identity_for_cardinality(task) -> str:
    """Return a durable reviewer identity for a closed Ship review.

    Legacy review messages predate structured completion evidence.  Prefer the
    structured actor when it exists, then the durable task assignment, and
    finally an id carried by the particular Ship message.  A display name is
    deliberately not an identity: a cardinality declaration must fail closed
    rather than treating two coincidentally named people as distinct reviewers.
    """
    review = _task_review_evidence(task)
    identity = str(review.get("agent_id", "") or "").strip()
    if identity:
        return identity
    identity = str(getattr(task, "agent_id", "") or "").strip()
    if identity:
        return identity
    for entry in reversed(getattr(task, "messages", []) or []):
        if str(entry.get("action", "") or "").lower() != "done":
            continue
        if _review_verdict_from_message(entry.get("message", "")) != "ship":
            continue
        identity = str(entry.get("agent_id", "") or "").strip()
        if identity:
            return identity
    return ""


def _reviewer_is_independent_for_cardinality(
        state: MatrixState, review_task, reviewer_id: str) -> bool:
    """Reject a Ship from anyone assigned to the reviewed task ancestry."""
    reviewer_id = str(reviewer_id or "").strip()
    if not reviewer_id:
        return False
    root_id = str(
        getattr(review_task, "pipeline_root_id", "") or ""
    ).strip()
    root = state.board_tasks.get(root_id) if root_id else None
    if (root and root is not review_task
            and str(getattr(root, "agent_id", "") or "").strip()
            == reviewer_id):
        return False
    seen = set()
    parent_id = str(getattr(review_task, "parent_task_id", "") or "").strip()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = state.board_tasks.get(parent_id)
        if not parent:
            break
        if str(getattr(parent, "agent_id", "") or "").strip() == reviewer_id:
            return False
        parent_id = str(getattr(parent, "parent_task_id", "") or "").strip()
    return True


def _legacy_review_cardinality_status(state: MatrixState, task) -> dict:
    """Evaluate an explicit legacy review-cardinality declaration.

    ``required_review_gates`` is the existing durable declaration slot.  In
    legacy mode its normalized entries declare *how many* independent Ship
    reviews are required; they do not activate the newer exact-boundary policy.
    Empty declarations intentionally retain the historic any-Ship predicate.
    """
    from .finalization import normalize_required_review_gates

    if not state or not task:
        return {"declared_count": 0, "satisfied_count": 0, "shortfall": 0,
                "eligible": True, "reviewer_ids": []}
    root_id = str(getattr(task, "pipeline_root_id", "") or task.id).strip()
    root = state.board_tasks.get(root_id, task)
    gates = normalize_required_review_gates(
        getattr(root, "required_review_gates", []) or [])
    declared_count = len(gates)
    if not declared_count:
        return {"declared_count": 0, "satisfied_count": 0, "shortfall": 0,
                "eligible": True, "reviewer_ids": []}

    reviewer_ids = set()
    for candidate in state.board_get_chain(root.id):
        if not _task_is_pipeline_descendant(state, root, candidate):
            continue
        if not task_counts_as_done(candidate) or not _is_feature_review_task(candidate):
            continue
        if not _review_task_has_ship_verdict(candidate):
            continue
        reviewer_id = _reviewer_identity_for_cardinality(candidate)
        if not _reviewer_is_independent_for_cardinality(
                state, candidate, reviewer_id):
            continue
        reviewer_ids.add(reviewer_id)
    satisfied_count = len(reviewer_ids)
    shortfall = max(0, declared_count - satisfied_count)
    return {
        "declared_count": declared_count,
        "satisfied_count": satisfied_count,
        "shortfall": shortfall,
        "eligible": shortfall == 0,
        "reviewer_ids": sorted(reviewer_ids),
    }


def _legacy_review_cardinality_error(status: dict) -> str:
    return (
        "Legacy review-cardinality gate is not satisfied: "
        f"declared count={int(status.get('declared_count', 0) or 0)}, "
        f"satisfied distinct count={int(status.get('satisfied_count', 0) or 0)}, "
        f"shortfall={int(status.get('shortfall', 0) or 0)}."
    )


def _mandatory_review_done_error(task, action_name: str) -> str:
    title = str(getattr(task, "task", "") or "").strip()
    if not title:
        title = str(getattr(task, "id", "") or "task").strip()
    title = title.replace("\\", "\\\\").replace('"', '\\"')
    action_name = str(action_name or "").strip() or "unknown"
    return (
        "This is a mandatory-review task "
        f"(action={action_name}). Direct `torque_done(...)` is blocked — "
        "derive `feature/review` first, then the reviewer's Ship verdict "
        "triggers cascade-done. Use:\n\n"
        f"  torque_derive(description=\"Review {title}\", action=\"feature/review\")"
    )


def _task_has_matching_deliverable_artifact(task) -> bool:
    """Return True if ``task`` already has a matching artifact attached.

    The match rule is intentionally lenient: when ``deliverable_type`` is
    empty or ``other``, ANY artifact/attachment satisfies the gate;
    otherwise the artifact's ``type`` (or ``artifact_type``) must equal
    ``deliverable_type``.
    """
    if not task:
        return False
    expected = str(getattr(task, "deliverable_type", "") or "").strip().lower()
    accept_any = expected in ("", "other")
    candidates = []
    candidates.extend(getattr(task, "artifacts", None) or [])
    candidates.extend(getattr(task, "attachments", None) or [])
    for entry in candidates:
        if not isinstance(entry, dict):
            return True if accept_any else False
        atype = str(
            entry.get("type", "")
            or entry.get("artifact_type", "")
            or ""
        ).strip().lower()
        if accept_any:
            return True
        if atype == expected:
            return True
    return False


def _reject_missing_deliverable(task, action_label: str) -> dict | None:
    """Reject ``torque_done`` / ``torque_ready`` when the deliverable is missing.

    ``action_label`` is the verb shown in the error message (``done`` or
    ``ready``).
    """
    if not task or not getattr(task, "deliverable_required", False):
        return None
    if _task_has_matching_deliverable_artifact(task):
        return None
    type_label = (
        str(getattr(task, "deliverable_type", "") or "").strip()
        or "any"
    )
    title_default = (
        str(getattr(task, "deliverable_artifact_title", "") or "").strip()
        or str(getattr(task, "task", "") or "").strip()
        or "deliverable"
    )
    artifact_type = (
        str(getattr(task, "deliverable_type", "") or "").strip()
        or "generated_doc"
    )
    word = deliverable_word(getattr(task, "deliverable_type", ""))
    return {
        "type": "deliverable_missing",
        "message": (
            f"Cannot mark task {action_label}: deliverable required "
            f"(type={type_label}) but no matching artifact attached. "
            f"Call `torque_task_upload_artifact(content_text=\"<your full "
            f"{word}>\", artifact_type=\"{artifact_type}\", "
            f"title=\"{title_default}\")` first, then retry "
            f"torque_{action_label}."
        ),
    }


def _task_upload_actor_source(state: MatrixState, actor, task) -> str:
    if not actor or getattr(actor, "cell_type", "") != "agent":
        return "agent"
    if str(getattr(actor, "kind", "") or "").strip() == "engineer":
        return "engineer"
    settings = state.get_group_settings(str(getattr(task, "group", "") or ""))
    if settings and str(getattr(settings, "engineer_agent_id", "") or "") == str(
            getattr(actor, "id", "") or ""):
        return "engineer"
    return "agent"


def _task_upload_engineer_scope_error(
        state: MatrixState, actor, task) -> dict | None:
    """Return a scoped-upload error for Engineer callers outside task scope."""
    if (
            not actor
            or getattr(actor, "cell_type", "") != "agent"
            or str(getattr(actor, "kind", "") or "").strip() != "engineer"):
        return None
    if state.engineer_can_access_task(
            str(getattr(actor, "id", "") or ""),
            task,
            allow_created=True,
            allow_unassigned=False,
    ):
        return None
    return {"type": "error", "message": "task not found in scope"}


def _reject_pending_review(task, action_label: str) -> dict | None:
    """Reject ``torque_done`` / ``torque_ready`` when a structural review is
    required and no reviewer-issued bypass is set (TORQUE:256).

    Workers cannot self-grant the bypass: ``pre_approved_by`` is set only
    when a reviewer derives a fix transition that declares
    ``pre_approved: true``. Workers must derive the required transition
    (e.g. ``feature/review``) and let cascade-done close the parent task.

    ``action_label`` is the verb shown in the error message (``done`` or
    ``ready``).
    """
    if not task or not getattr(task, "requires_review", False):
        return None
    if str(getattr(task, "pre_approved_by", "") or "").strip():
        return None
    title = str(getattr(task, "task", "") or "").strip()
    if not title:
        title = str(getattr(task, "id", "") or "task").strip()
    title = title.replace("\\", "\\\\").replace('"', '\\"')
    return {
        "type": "review_required",
        "message": (
            f"Cannot mark task {action_label}: review required by action "
            "contract. This task carries requires_review=true (declared by "
            "its action's transitions[required: true]). Derive the review "
            f"transition before calling torque_{action_label}; the reviewer's "
            "Ship verdict will cascade-close the parent. If this is a fix "
            "to a previously-reviewed change, the reviewer must derive via "
            "a `pre_approved: true` transition to grant a structural "
            "bypass — workers cannot self-grant it.\n\n"
            f"  torque_derive(description=\"Review {title}\", "
            "action=\"feature/review\")"
        ),
    }


def _reject_mandatory_review_done_without_ship(
        state: MatrixState,
        action_mgr: ActionManager | None,
        cell,
        task,
        *,
        base_dir: str = "") -> dict | None:
    """Reject worker direct-done on mandatory review-pipeline tasks."""
    if not state or not cell or not task:
        return None
    if agent_kind_for_identity(cell) != "worker":
        return None
    # A nonempty durable declaration is a legacy compatibility contract, not
    # prose inference.  It must be checked before the older action heuristic
    # so a root cannot self-close after only one of several required reviews.
    root_id = str(getattr(task, "pipeline_root_id", "") or task.id).strip()
    if root_id == str(getattr(task, "id", "") or "").strip():
        cardinality = _legacy_review_cardinality_status(state, task)
        if cardinality["declared_count"] and not cardinality["eligible"]:
            return {
                "type": "error",
                "message": _legacy_review_cardinality_error(cardinality),
            }
    # Reviewer-issued pre-approval bypass (TORQUE:256) — when the parent
    # review derived this task via a ``pre_approved: true`` transition,
    # the structural flag overrides this heuristic gate too. Workers
    # cannot self-grant the bypass; ``pre_approved_by`` is set only by
    # ``ai_report.derive`` when the chosen transition carries the flag.
    if str(getattr(task, "pre_approved_by", "") or "").strip():
        return None

    action_name = str(getattr(task, "action_name", "") or "").strip()
    if not _action_requires_mandatory_feature_review(
            action_mgr, action_name, base_dir):
        return None
    if _task_has_shipped_review_descendant(state, task):
        return None
    return {
        "type": "error",
        "message": _mandatory_review_done_error(task, action_name),
    }


def _review_gate_diff_size(summary: dict) -> int:
    """Return insertions + deletions from a diff summary dict."""
    try:
        insertions = int((summary or {}).get("insertions", 0) or 0)
    except (TypeError, ValueError):
        insertions = 0
    try:
        deletions = int((summary or {}).get("deletions", 0) or 0)
    except (TypeError, ValueError):
        deletions = 0
    return max(0, insertions) + max(0, deletions)


def _review_gate_skip_audit_message(cell, task, *,
                                    diff_size: int,
                                    threshold: int,
                                    reason: str) -> str:
    worker_id = str(getattr(cell, "id", "") or "").strip()
    worker_name = str(getattr(cell, "name", "") or "").strip()
    task_id = str(getattr(task, "id", "") or "").strip()
    reason = str(reason or "").strip() or "force-skip-review"
    worker = worker_id
    if worker_name:
        worker = f"{worker_id} ({worker_name})" if worker_id else worker_name
    return (
        "Review gate skipped by worker "
        f"{worker} for task {task_id}: diff size {diff_size} LOC, "
        f"threshold {threshold}; reason: {reason}"
    )


async def _maybe_apply_review_required_gate(
        state: MatrixState,
        action_mgr: ActionManager,
        worktree_mgr: WorktreeManager,
        handle_command,
        panel_event,
        *,
        cell,
        task,
        base_dir: str = "",
        force_skip_review: bool = False,
        skip_reason: str = "",
        checkpoint_for_gate=None,
        append_task_msg=None,
        record_history_msg=None) -> dict | None:
    """Enforce action-level review-required-above-LOC metadata.

    Returns an error result when direct completion is refused, otherwise None
    so the caller can proceed with the normal closeout path.
    """
    if not state or not cell or not task or not task.action_name:
        return None
    # Reviewer-issued pre-approval bypass (TORQUE:256) — a derived fix
    # task carrying ``pre_approved_by`` skips the LOC gate too, since the
    # reviewer already determined the change ships without re-review.
    if str(getattr(task, "pre_approved_by", "") or "").strip():
        return None

    act = action_mgr.load_action(task.action_name, base_dir)
    if not _action_is_implementation_depth(act):
        return None
    transition_policy = _review_gate_transition_policy(act)
    action_policy = _review_gate_policy_from_action_threshold(
        _explicit_review_gate_threshold_from_action(act)
    )
    architect_policy = _review_gate_architect_policy(state, task, cell)
    gate_policy = transition_policy or action_policy
    if (
            gate_policy
            and gate_policy.get("source") == "action"
            and architect_policy):
        # Action-level review_required_above_loc is a legacy threshold-only
        # setting.  Preserve existing architect control over whether workers
        # may self-review-bypass while still letting the action threshold win.
        gate_policy = dict(gate_policy)
        gate_policy["controls_self_review_bypass"] = True
        gate_policy["self_review_bypass_allowed"] = bool(
            architect_policy.get("self_review_bypass_allowed", False)
        )
        gate_policy["bypass_source"] = "architect"
        gate_policy["bypass_architect_id"] = architect_policy.get(
            "architect_id", "")
    if not gate_policy:
        gate_policy = architect_policy
    if not gate_policy:
        gate_policy = _review_gate_policy_from_action_threshold(
            _review_gate_threshold_from_action(act)
        )
        if gate_policy:
            gate_policy["source"] = "default"
    if not gate_policy:
        return None
    threshold = int(gate_policy.get("threshold", 0) or 0)

    if _chain_has_shipped_review(state, task):
        return None

    if checkpoint_for_gate:
        await checkpoint_for_gate()

    try:
        diff_summary = await worktree_mgr.diff_summary(
            cell,
            non_test_only=True,
        )
    except TypeError:
        # Test doubles or older integrations may not accept the keyword.
        diff_summary = await worktree_mgr.diff_summary(cell)
    diff_size = _review_gate_diff_size(diff_summary)
    if diff_size <= threshold:
        return None

    if (
            force_skip_review
            and gate_policy.get("controls_self_review_bypass")
            and not gate_policy.get("self_review_bypass_allowed")):
        force_skip_review = False
        bypass_source = (
            gate_policy.get("bypass_source")
            or gate_policy.get("source", "review-gate")
        )
        skip_reason = (
            "self-review bypass disabled by "
            f"{bypass_source} settings"
        )

    if force_skip_review:
        reason = str(skip_reason or "").strip() or "force-skip-review"
        audit = _review_gate_skip_audit_message(
            cell,
            task,
            diff_size=diff_size,
            threshold=threshold,
            reason=reason,
        )
        if append_task_msg:
            append_task_msg(task, "review_gate_skipped", audit, cell.name)
        elif task:
            task.messages.append({
                "timestamp": time.time(),
                "action": "review_gate_skipped",
                "message": audit,
                "agent_name": getattr(cell, "name", ""),
            })
        if record_history_msg:
            record_history_msg(
                cell,
                "review_gate_skipped",
                audit,
                task_override=task,
            )
        if panel_event:
            panel_event(
                "review_gate_skipped",
                cell.id,
                cell.name,
                cell.group,
                audit,
                task_id=task.id,
            )
            _emit_workflow_breach_event(
                state,
                panel_event,
                subkind="escape_clause_skip",
                source="auto",
                task=task,
                worker=cell,
                context=audit,
            )
        return None

    title = f"Review required — diff exceeded {threshold} LOC threshold"
    context = (
        f"Review required — diff exceeded {threshold} LOC threshold. "
        "Please review and return Ship / Ship with fixes / Revert.\n\n"
        "Gate details:\n"
        f"- Worker: {cell.id} ({cell.name})\n"
        f"- Task: {task.id}\n"
        f"- Diff: {diff_size} non-test LOC "
        f"({(diff_summary or {}).get('insertions', 0)} insertions + "
        f"{(diff_summary or {}).get('deletions', 0)} deletions across "
        f"{(diff_summary or {}).get('files', 0)} non-test files)\n"
        f"- Threshold: {threshold}\n"
    )
    if gate_policy.get("source") == "transition":
        context += (
            "- Transition LOC gate: "
            f"action={gate_policy.get('action') or _REVIEW_GATE_ACTION}, "
            f"ship_direct_max={gate_policy['ship_direct_max']}, "
            f"review_default_above={gate_policy['review_default_above']}, "
            "self_review_bypass_allowed="
            f"{gate_policy['self_review_bypass_allowed']}\n"
        )
        if skip_reason:
            context += f"- Skip request ignored: {skip_reason}\n"
    elif gate_policy.get("source") == "architect":
        context += (
            "- Architect review policy: "
            f"architect={gate_policy['architect_id']}, "
            f"ship_direct_max={gate_policy['ship_direct_max']}, "
            f"review_default_above={gate_policy['review_default_above']}, "
            "self_review_bypass_allowed="
            f"{gate_policy['self_review_bypass_allowed']}\n"
        )
        if skip_reason:
            context += f"- Skip request ignored: {skip_reason}\n"
    elif skip_reason:
        context += f"- Skip request ignored: {skip_reason}\n"
    derive_result = await handle_command({
        "cmd": "ai_report",
        "cell_id": cell.id,
        "action": "derive",
        "task_id": task.id,
        "action_name": _REVIEW_GATE_ACTION,
        "message": title,
        "description": context,
        "_review_gate": True,
    })
    if derive_result and derive_result.get("type") == "error":
        return {
            "type": "error",
            "message": (
                "Cannot close directly — review gate required "
                f"(diff: {diff_size} LOC, threshold: {threshold}), but "
                "auto-deriving `feature/review` failed: "
                f"{derive_result.get('message', 'unknown error')}"
            ),
        }

    review_task_id = (derive_result or {}).get("task_id", "")
    breach_context = (
        "Review gate auto-derived "
        f"{review_task_id or _REVIEW_GATE_ACTION} after direct done attempt; "
        f"diff {diff_size} non-test LOC exceeded threshold {threshold}."
    )
    _emit_workflow_breach_event(
        state,
        panel_event,
        subkind="escape_clause_skip",
        source="auto",
        task=task,
        worker=cell,
        context=breach_context,
    )
    review_task_label = review_task_id or "the review task"
    return {
        "type": "error",
        "message": (
            "Cannot close directly — `feature/review` auto-derived at "
            f"{review_task_label} per action gate (diff: {diff_size} LOC, "
            f"threshold: {threshold}). Wait for reviewer's Ship verdict "
            "before calling `torque_done(...)` again."
        ),
        "task_id": review_task_id,
        "review_gate": {
            "diff_size": diff_size,
            "threshold": threshold,
            "review_task_id": review_task_id,
        },
    }
