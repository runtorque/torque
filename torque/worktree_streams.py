"""Computed worktree stream synthesis helpers.

Phase 1 keeps streams as a read-only computed model derived from existing
task, boundary, verification, and agent state. No persisted stream table is
introduced here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from .state import board_task_is_closed
from .task_ids import parse_task_id
from .worktree_stream_readiness import (
    _branch_exists_locally,
    _list_repo_branches_async,
    _merge_readiness_cache_get,
    _merge_readiness_cache_put,
    _probe_merge_readiness,
    invalidate_branch_exists_cache,
    prefill_branch_exists_async,
    prefill_branch_exists_for_state,
    prefill_merge_readiness_for_state,
    prefill_merge_readiness_for_streams,
)
from .worktree_boundaries import (
    boundary_pr_metadata,
    branch_boundary_tasks,
    branch_key,
    queued_successor_tasks,
    started_successor_tasks,
    task_boundary,
    task_branch_key,
)

_QUEUED_LANES = {"Backlog", "To Do"}
_VISIBILITY_LABELS = {"torque:engineer-message"}
_WORKFLOW_LABELS = {"torque:derived", "review-fix", "torque:human"}
_HELD_LABELS = {"torque:hold", "hold"}
_REVIEW_HINTS = ("review", "re-review")
_VALIDATION_HINTS = ("validate", "validation", "manual smoke", "smoke test")
_MERGE_CONFLICT_HINTS = ("merge conflict", "resolve conflict", "conflict")
_PRODUCT_ACTIONS = {
    "feature/implement",
    "oneshot/feature",
    "oneshot/fix",
    "oneshot/improvement",
}
_NON_PRODUCT_ACTION_HINTS = (
    "review",
    "validate",
    "validation",
    "merge",
    "conflict",
    "research",
)
_STALE_PR_MERGE_STATES = {"BEHIND"}
_VERIFICATION_SUMMARY_TEXT_KEYS = (
    "tests_run",
    "human_validation_pending",
    "isolated_rerun_evidence",
    "test_outcome",
    "reviewer_acceptance",
)
_VERIFICATION_SUMMARY_BOOL_KEYS = (
    "manual_smoke_done",
    "deploy_needed",
    "deploy_attempted",
    "full_suite_attempted",
    "unrelated_flake_accepted",
    "live_smoke_pending",
)


def _task_review_evidence(task) -> dict:
    evidence = getattr(task, "completion_evidence", {}) or {}
    if not isinstance(evidence, dict):
        return {}
    review = evidence.get("review", {}) or {}
    if not isinstance(review, dict):
        return {}
    verdict = str(review.get("verdict", "") or "").strip()
    if not verdict:
        return {}
    keys = (
        "verdict",
        "follow_up_classification",
        "source_action",
        "recorded_at",
        "derived_action",
        "derived_task_id",
        "notes",
        "summary",
        "follow_up_notes",
    )
    return {
        key: str(review.get(key, "") or "").strip()
        for key in keys
        if str(review.get(key, "") or "").strip()
    }


def _task_packet_ref(task) -> dict:
    if not task:
        return {}
    return {
        "task_id": getattr(task, "id", "") or "",
        "task_title": getattr(task, "task", "") or "",
        "lane": getattr(task, "lane", "") or "",
    }


def _task_refs(tasks: Iterable) -> list[dict]:
    return [_task_packet_ref(task) for task in tasks if task]


def _completion_deviation_packets(tasks: Iterable) -> list[dict]:
    """Return worker-attested completion deviations for merge decisioning.

    This intentionally reads the typed completion record only. Acceptance
    criteria are prose, so trying to derive deviations from task text or a
    free-form completion summary would fabricate a decision signal.
    """
    packets = []
    seen_task_ids = set()
    for task in tasks:
        task_id = str(getattr(task, "id", "") or "").strip()
        if not task or task_id in seen_task_ids:
            continue
        seen_task_ids.add(task_id)
        evidence = getattr(task, "completion_evidence", {}) or {}
        if not isinstance(evidence, dict):
            continue
        completion = evidence.get("completion", {}) or {}
        if not isinstance(completion, dict):
            continue
        deviation = completion.get("acceptance_deviation", {}) or {}
        if not isinstance(deviation, dict):
            continue
        statement = str(deviation.get("statement", "") or "").strip()
        reason = str(deviation.get("reason", "") or "").strip()
        if not statement or not reason:
            continue
        packets.append({
            "task_id": task_id,
            "task_title": str(getattr(task, "task", "") or "").strip(),
            "statement": statement,
            "reason": reason,
            "agent_id": str(deviation.get("agent_id", "") or "").strip(),
            "agent_name": str(deviation.get("agent_name", "") or "").strip(),
            "recorded_at": str(deviation.get("recorded_at", "") or "").strip(),
        })
    return packets


def _completion_deviation_disclosure_attempt_packets(
        tasks: Iterable) -> list[dict]:
    """Return incomplete worker disclosure attempts without attesting one.

    This is deliberately separate from ``_completion_deviation_packets``:
    only a complete statement/reason pair is a worker-attested acceptance
    deviation.  The partial signal exists so Architect merge/acceptance
    surfaces can distinguish an attempted disclosure from an ordinary close.
    """
    packets = []
    seen_task_ids = set()
    for task in tasks:
        task_id = str(getattr(task, "id", "") or "").strip()
        if not task or task_id in seen_task_ids:
            continue
        seen_task_ids.add(task_id)
        evidence = getattr(task, "completion_evidence", {}) or {}
        if not isinstance(evidence, dict):
            continue
        completion = evidence.get("completion", {}) or {}
        if not isinstance(completion, dict):
            continue
        attempt = completion.get("acceptance_deviation_attempt", {}) or {}
        if not isinstance(attempt, dict):
            continue
        statement = str(attempt.get("statement", "") or "").strip()
        reason = str(attempt.get("reason", "") or "").strip()
        missing_fields = [
            field for field in (attempt.get("missing_fields", []) or [])
            if field in {"statement", "reason"}
        ]
        if not (statement or reason) or not missing_fields:
            continue
        packets.append({
            "task_id": task_id,
            "task_title": str(getattr(task, "task", "") or "").strip(),
            "statement": statement,
            "reason": reason,
            "missing_fields": missing_fields,
            "agent_id": str(attempt.get("agent_id", "") or "").strip(),
            "agent_name": str(attempt.get("agent_name", "") or "").strip(),
            "recorded_at": str(attempt.get("recorded_at", "") or "").strip(),
        })
    return packets


def _verification_packet(validation_state: str,
                         validation_record: dict | None) -> dict:
    record = validation_record or {}
    summary = record.get("summary", {}) if isinstance(record, dict) else {}
    if not isinstance(summary, dict):
        summary = {}

    normalized_summary = {}
    for key in _VERIFICATION_SUMMARY_TEXT_KEYS:
        value = str(summary.get(key, "") or "").strip()
        if value:
            normalized_summary[key] = value
    for key in _VERIFICATION_SUMMARY_BOOL_KEYS:
        if key in summary:
            normalized_summary[key] = bool(summary.get(key))

    packet = {
        "state": validation_state,
        "task_id": str(record.get("task_id", "") or "").strip(),
        "verification_state": str(
            record.get("verification_state", "") or ""
        ).strip(),
        "verification_notes": str(
            record.get("verification_notes", "") or ""
        ).strip(),
        "summary": normalized_summary,
    }
    return {
        key: value
        for key, value in packet.items()
        if value not in ("", {}, None)
    }


def _review_packet(review_boundary, latest_review: dict) -> dict:
    packet = dict(latest_review or {})
    if review_boundary:
        packet.setdefault("task_id", getattr(review_boundary, "id", "") or "")
        packet.setdefault(
            "task_title", getattr(review_boundary, "task", "") or ""
        )
        packet.setdefault("lane", getattr(review_boundary, "lane", "") or "")
    return {
        key: value
        for key, value in packet.items()
        if value not in ("", {}, None)
    }


def _follow_up_note_packets(latest_review: dict) -> dict:
    if not isinstance(latest_review, dict) or not latest_review:
        return {}
    classification = str(
        latest_review.get("follow_up_classification", "") or ""
    ).strip().lower()
    if classification in {"none", ""}:
        return {}

    note = str(
        latest_review.get("follow_up_notes")
        or latest_review.get("notes")
        or latest_review.get("summary")
        or ""
    ).strip()
    if not note:
        derived = str(latest_review.get("derived_task_id", "") or "").strip()
        note = f"Follow-up derived: {derived}" if derived else classification

    bucket = {
        "blocking": "blocking",
        "block": "blocking",
        "non_blocking": "non_blocking",
        "non-blocking": "non_blocking",
        "future_context": "future_context",
        "future-context": "future_context",
    }.get(classification, classification)
    if bucket not in {"blocking", "non_blocking", "future_context"}:
        bucket = "non_blocking"
    return {bucket: [note]}


def _stale_base_packet(boundary: dict, pr: dict) -> dict:
    base_branch = str(
        (boundary or {}).get("base_branch", "")
        or (pr or {}).get("base_branch", "")
        or ""
    ).strip()
    repo_root = str((boundary or {}).get("repo_root", "") or "").strip()
    branch = str(
        (boundary or {}).get("branch", "")
        or (pr or {}).get("head_branch", "")
        or ""
    ).strip()
    # A fresh probe always outranks historical boundary/PR metadata.  The
    # latter describes what was true when review ran; it cannot prove the
    # base is still current now.
    cached = _merge_readiness_cache_get(repo_root, branch, base_branch)
    if cached:
        return cached

    stale = boundary.get("stale_base") if isinstance(boundary, dict) else {}
    if isinstance(stale, dict) and stale:
        packet = dict(stale)
        # A historical stale finding remains a safe reason to block or
        # rebase.  A historical *fresh* finding is not proof that the base is
        # still current, so only a live cache probe may make a stream ready.
        if packet.get("stale"):
            packet["state"] = "stale"
            packet["source"] = packet.get("source") or "boundary"
            return packet

    pr_merge_state = str((pr or {}).get("merge_state", "") or "").strip()
    if pr_merge_state.upper() in _STALE_PR_MERGE_STATES:
        return {
            "state": "stale",
            "stale": True,
            "source": "pr_merge_state",
            "merge_state": pr_merge_state,
        }
    return {"state": "unknown", "stale": None, "source": "not_checked"}


def _current_head_packet(*, reviewed_sha: str, branch_advanced: bool,
                         boundary: dict, pr: dict,
                         readiness: dict) -> dict:
    readiness_branch_head = ""
    if str((readiness or {}).get("source", "") or "").strip() == (
            "merge_readiness_check"):
        readiness_branch_head = str(
            (readiness or {}).get("branch_head", "") or ""
        ).strip()
    pr_head_sha = str((pr or {}).get("head_sha", "") or "").strip()
    current = str(
        readiness_branch_head
        or (boundary or {}).get("head_sha", "")
        or (boundary or {}).get("current_head_sha", "")
        or pr_head_sha
        or ""
    ).strip()
    source = ""
    if current:
        if current == readiness_branch_head:
            source = "merge_readiness_check"
        elif current == str((boundary or {}).get("head_sha", "") or "").strip():
            source = "merge_check"
        elif current == str(
            (boundary or {}).get("current_head_sha", "") or ""
        ).strip():
            source = "boundary_current_head"
        else:
            source = "pull_request"
    else:
        source = "unknown"
    return {
        "reviewed_boundary_sha": reviewed_sha,
        "current_branch_head_sha": current,
        "current_branch_head_sha_source": source,
        "current_branch_head_sha_verified": bool(current),
        "branch_advanced": branch_advanced,
    }


def merge_report_snippet_template(packet: dict | None = None) -> str:
    packet = packet or {}
    task_ids = ", ".join(packet.get("product_task_ids", []) or []) or "<task ids>"
    branch = str(packet.get("branch", "") or "<branch>")
    reviewed_sha = str(
        ((packet.get("head", {}) or {}).get("reviewed_boundary_sha", ""))
        or "<reviewed_sha>"
    )
    verification = packet.get("verification", {}) or {}
    verification_summary = str(
        verification.get("state")
        or verification.get("verification_state")
        or "<verification_summary>"
    )
    return "\n".join([
        "Merge report:",
        f"- Stream: {branch}",
        f"- Product tasks: {task_ids}",
        f"- Reviewed SHA: {reviewed_sha}",
        "- PR: <pr_url>",
        "- Merged SHA: <merge_sha>",
        "- Origin verification: <origin_verification>",
        f"- Verification: {verification_summary}",
        "- Follow-ups: <followups_or_none>",
    ])


def merge_report_snippet_from_merge_result(result: dict | None,
                                           *, branch: str = "",
                                           base_branch: str = "") -> str:
    result = result or {}
    pr = result.get("pr") if isinstance(result.get("pr"), dict) else {}
    origin = (
        result.get("origin_verification")
        if isinstance(result.get("origin_verification"), dict)
        else {}
    )
    pr_url = str(
        result.get("pr_url")
        or result.get("url")
        or pr.get("url")
        or ""
    ).strip()
    merge_sha = str(
        result.get("sha")
        or result.get("merge_commit_sha")
        or pr.get("merge_commit_sha")
        or ""
    ).strip()
    head_sha = str(
        result.get("head_sha")
        or pr.get("head_sha")
        or ""
    ).strip()
    origin_status = str(
        origin.get("status")
        or origin.get("state")
        or ("provided" if origin else "")
    ).strip()
    return "\n".join([
        "Merge report:",
        f"- Stream: {branch or result.get('branch', '') or '<branch>'}",
        f"- Base: {base_branch or result.get('base_branch', '') or '<base>'}",
        f"- PR: {pr_url or '<pr_url>'}",
        f"- Reviewed/head SHA: {head_sha or '<reviewed_or_head_sha>'}",
        f"- Merged SHA: {merge_sha or '<merge_sha>'}",
        f"- Origin verification: {origin_status or '<origin_verification>'}",
        f"- Result: {str(result.get('message') or '').strip() or '<merge result>'}",
    ])


def _merge_readiness_next_action(*, stream_state: str, stale_base: dict,
                                 branch_advanced: bool,
                                 recommended_next_action: str) -> str:
    if stream_state == "merged":
        return "none"
    if stream_state == "merge_readiness_unknown":
        return "check_merge_readiness"
    if bool((stale_base or {}).get("stale")):
        return "rebase"
    if recommended_next_action in {
        "resolve_merge_conflict",
        "address_review_blockers",
        "wait_for_review",
        "run_manual_validation",
        "merge_after_validation",
    }:
        return recommended_next_action
    if branch_advanced:
        return "re-review"
    return recommended_next_action


def _recommended_worktree_tool(recommended_next_action: str) -> str:
    """Return the canonical MCP operation for an actionable stream hint."""

    return {
        "merge": "worktree_merge",
        "rebase": "worktree_rebase",
    }.get(str(recommended_next_action or "").strip(), "")


def _merge_readiness_packet(*, stream_id_value: str, repo_root: str,
                            branch: str, stream_state: str,
                            merge_state: str, code_state: str,
                            product_tasks: list[Any],
                            workflow_tasks: list[Any],
                            queued_followers: list[Any],
                            started_followers: list[Any],
                            latest_review_boundary,
                            latest_boundary_info: dict,
                            latest_review: dict,
                            latest_reviewed_commit_sha: str,
                            latest_merged_commit_sha: str,
                            latest_boundary_pr: dict,
                            head: dict,
                            branch_has_advanced: bool,
                            partial_review_safe: bool,
                            validation_state: str,
                            validation_record: dict,
                            active_blocker_task,
                            blocker_parent_review_task,
                            gate_reason: str,
                            recommended_next_action: str) -> dict:
    stale_base = _stale_base_packet(latest_boundary_info, latest_boundary_pr)
    next_action = _merge_readiness_next_action(
        stream_state=stream_state,
        stale_base=stale_base,
        branch_advanced=branch_has_advanced,
        recommended_next_action=recommended_next_action,
    )
    active_workflow_tasks = [
        task for task in workflow_tasks if not board_task_is_closed(task)
    ]
    completion_deviation_attempts = (
        _completion_deviation_disclosure_attempt_packets(
            [*product_tasks, *workflow_tasks]
        )
    )
    boundary_status = str(
        (latest_boundary_info or {}).get("status", "") or ""
    ).strip()
    packet = {
        "version": 1,
        "stream_id": stream_id_value,
        "repo_root": repo_root,
        "branch": branch,
        "state": stream_state,
        "code_state": code_state,
        "merge_state": merge_state,
        "product_task_ids": [task.id for task in product_tasks],
        "workflow_task_ids": [task.id for task in workflow_tasks],
        "completion_deviations": _completion_deviation_packets(
            [*product_tasks, *workflow_tasks]
        ),
        "active_workflow_task_ids": [
            task.id for task in active_workflow_tasks
        ],
        "latest_reviewed_boundary": {
            "task_id": getattr(latest_review_boundary, "id", "") or "",
            "task_title": getattr(latest_review_boundary, "task", "") or "",
            "lane": getattr(latest_review_boundary, "lane", "") or "",
            "status": boundary_status,
            "recorded_at": (
                (latest_boundary_info or {}).get("recorded_at", "") or ""
            ),
            "reviewed_sha": latest_reviewed_commit_sha,
        },
        "head": dict(head or {}),
        "stale_base": stale_base,
        "review_final": _review_packet(latest_review_boundary, latest_review),
        "verification": _verification_packet(
            validation_state,
            validation_record,
        ),
        "followups": {
            "queued": _task_refs(queued_followers),
            "started": _task_refs(started_followers),
            "queued_count": len(queued_followers),
            "started_count": len(started_followers),
            "active_blocker_fix_task": _task_packet_ref(active_blocker_task),
            "blocker_parent_review_task": _task_packet_ref(
                blocker_parent_review_task
            ),
            "notes": _follow_up_note_packets(latest_review),
        },
        "pr": latest_boundary_pr,
        "latest_merged_commit_sha": latest_merged_commit_sha,
        "branch_advanced": branch_has_advanced,
        "partial_review_safe": partial_review_safe,
        "gate_reason": gate_reason,
        "recommended_next_action": next_action,
        "recommended_tool": _recommended_worktree_tool(next_action),
    }
    if completion_deviation_attempts:
        packet["completion_deviation_disclosure_attempts"] = (
            completion_deviation_attempts
        )
    packet["merge_report_snippet"] = merge_report_snippet_template(packet)
    return packet


def stream_id(repo_root: str, branch: str) -> str:
    """Return the canonical computed stream id for a branch/worktree slice."""
    return f"stream:{branch_key(repo_root, branch)}"


def stream_identity_for_agent(cell) -> tuple[str, str] | None:
    """Return ``(repo_root, branch)`` for an agent with an active worktree."""
    if not cell:
        return None
    repo_root = str(
        getattr(cell, "worktree_repo_root", "")
        or getattr(cell, "git_root", "")
        or ""
    ).strip()
    branch = str(getattr(cell, "worktree_branch", "") or "").strip()
    if not repo_root or not branch:
        return None
    return repo_root, branch


def classify_stream_task(task, tasks_by_id: dict[str, Any] | None = None) -> str:
    """Classify a task as ``product``, ``workflow``, or ``visibility``."""
    if _is_visibility_task(task):
        return "visibility"
    if _is_workflow_task(task, tasks_by_id or {}):
        return "workflow"
    return "product"


def member_task_ids_for_stream(stream: dict) -> set[str]:
    """Return the known task ids referenced by a computed stream payload."""
    ids: set[str] = set()
    for key in (
        "product_task_ids",
        "workflow_task_ids",
        "queued_task_ids",
        "started_task_ids",
    ):
        ids.update(
            str(task_id or "").strip()
            for task_id in stream.get(key, []) or []
            if str(task_id or "").strip()
        )
    for key in (
        "foreground_task_id",
        "latest_boundary_task_id",
        "active_review_task_id",
        "active_blocker_task_id",
        "blocker_parent_review_task_id",
        "ready_to_resume_task_id",
    ):
        task_id = str(stream.get(key, "") or "").strip()
        if task_id:
            ids.add(task_id)
    for item in stream.get("recent_visibility_items", []) or []:
        task_id = str((item or {}).get("task_id", "") or "").strip()
        if task_id:
            ids.add(task_id)
    for item in stream.get("queue_items", []) or []:
        task_id = str((item or {}).get("task_id", "") or "").strip()
        if task_id:
            ids.add(task_id)
    gate = stream.get("queue_gate", {}) or {}
    for key in ("blocking_task_id", "source_task_id"):
        task_id = str(gate.get(key, "") or "").strip()
        if task_id:
            ids.add(task_id)
    return ids


def compute_worktree_stream_for_task(state, task_id: str, *, group: str = "",
                                     visibility_limit: int = 10) -> dict | None:
    """Return the computed stream containing ``task_id`` when one exists."""
    task_id = str(task_id or "").strip()
    if not task_id:
        return None
    for stream in compute_worktree_streams(
            state,
            group=group,
            visibility_limit=visibility_limit):
        if task_id in member_task_ids_for_stream(stream):
            return stream
    return None


def compute_worktree_streams(state, *, group: str = "",
                             visibility_limit: int = 10,
                             include_orphaned: bool = True) -> list[dict]:
    """Return computed streams for ``state`` filtered to ``group`` when set.

    Archived tasks never participate in live streams, so they're filtered
    here. When ``group`` is set, the per-group index is used to avoid
    walking the full task table.
    """
    if group and hasattr(state, "tasks_in_group"):
        candidate_tasks = state.tasks_in_group(group)
    else:
        candidate_tasks = state.board_tasks.values()
    tasks = [
        task for task in candidate_tasks
        if getattr(task, "lane", "") != "Archived"
    ]
    agents = [
        cell for cell in state.iter_active_agents()
        if getattr(cell, "cell_type", "") == "agent"
        and (not group or getattr(cell, "group", "") == group)
    ]
    if not tasks and not agents:
        return []

    tasks_by_id = {
        getattr(task, "id", ""): task
        for task in tasks
        if getattr(task, "id", "")
    }
    children_by_parent: dict[str, set[str]] = defaultdict(set)
    boundary_task_to_stream_key: dict[str, str] = {}
    boundary_to_successors: dict[str, set[str]] = defaultdict(set)
    seeds_by_stream_key: dict[str, set[str]] = defaultdict(set)
    agent_ids_by_stream_key: dict[str, set[str]] = defaultdict(set)
    agent_stream_key_by_id: dict[str, str] = {}
    branch_exists_cache: dict[tuple[str, str], bool] = {}

    for task in tasks:
        task_id = getattr(task, "id", "") or ""
        parent_id = getattr(task, "parent_task_id", "") or ""
        if parent_id:
            children_by_parent[parent_id].add(task_id)

        key = task_branch_key(task)
        if key:
            boundary_task_to_stream_key[task_id] = key
            seeds_by_stream_key[key].add(task_id)

    for task in tasks:
        task_id = getattr(task, "id", "") or ""
        boundary_task_id = (
            getattr(task, "resume_after_boundary_task_id", "") or ""
        )
        if boundary_task_id:
            boundary_to_successors[boundary_task_id].add(task_id)
            stream_key = boundary_task_to_stream_key.get(boundary_task_id, "")
            if stream_key:
                seeds_by_stream_key[stream_key].add(task_id)

    for cell in agents:
        identity = stream_identity_for_agent(cell)
        if not identity:
            continue
        repo_root, branch = identity
        key = branch_key(repo_root, branch)
        agent_stream_key_by_id[cell.id] = key
        agent_ids_by_stream_key[key].add(cell.id)
        current_task_id = str(getattr(cell, "current_task_id", "") or "").strip()
        if current_task_id in tasks_by_id:
            seeds_by_stream_key[key].add(current_task_id)

    task_explicit_stream_keys = _build_task_explicit_stream_keys(
        tasks=tasks,
        boundary_task_to_stream_key=boundary_task_to_stream_key,
        agent_stream_key_by_id=agent_stream_key_by_id,
    )
    task_descendant_stream_keys = _build_task_descendant_stream_keys(
        tasks_by_id=tasks_by_id,
        children_by_parent=children_by_parent,
        task_explicit_stream_keys=task_explicit_stream_keys,
    )

    for task in tasks:
        reply_agent_id = str(getattr(task, "reply_agent_id", "") or "").strip()
        if reply_agent_id:
            reply_agent = state.agents.get(reply_agent_id)
            identity = stream_identity_for_agent(reply_agent)
            if identity:
                seeds_by_stream_key[branch_key(*identity)].add(task.id)
        agent_id = str(getattr(task, "agent_id", "") or "").strip()
        if agent_id:
            owner = state.agents.get(agent_id)
            identity = stream_identity_for_agent(owner)
            if identity:
                seeds_by_stream_key[branch_key(*identity)].add(task.id)

    streams = []
    for key in sorted(seeds_by_stream_key):
        if not seeds_by_stream_key[key]:
            continue
        repo_root, branch = key.split("::", 1)
        member_ids = _expand_stream_task_ids(
            seed_ids=seeds_by_stream_key[key],
            target_stream_key=key,
            tasks_by_id=tasks_by_id,
            children_by_parent=children_by_parent,
            boundary_to_successors=boundary_to_successors,
            task_explicit_stream_keys=task_explicit_stream_keys,
            task_descendant_stream_keys=task_descendant_stream_keys,
        )
        if not member_ids:
            continue
        stream = compute_worktree_stream(
            state,
            repo_root=repo_root,
            branch=branch,
            group=group,
            visibility_limit=visibility_limit,
            task_ids=member_ids,
            stream_agent_ids=agent_ids_by_stream_key.get(key, set()),
            branch_exists_cache=branch_exists_cache,
        )
        if stream and (
            include_orphaned
            or stream.get("stream_presence", "") != "orphaned"
        ):
            streams.append(stream)

    streams.sort(
        key=lambda item: (
            -_parse_iso(item.get("last_activity_at", "")),
            item.get("branch", ""),
            item.get("stream_id", ""),
        )
    )
    return streams


def compute_worktree_stream(state, *, repo_root: str, branch: str,
                            group: str = "", visibility_limit: int = 10,
                            task_ids: set[str] | None = None,
                            stream_agent_ids: set[str] | None = None,
                            branch_exists_cache: dict[tuple[str, str], bool]
                            | None = None,
                            ) -> dict | None:
    """Return one computed stream for ``repo_root`` + ``branch``."""
    if not repo_root or not branch:
        return None

    if group and hasattr(state, "tasks_in_group"):
        candidate_tasks = state.tasks_in_group(group)
    else:
        candidate_tasks = state.board_tasks.values()
    tasks_by_id = {
        getattr(task, "id", ""): task
        for task in candidate_tasks
        if getattr(task, "id", "")
        and getattr(task, "lane", "") != "Archived"
    }
    if task_ids is None:
        children_by_parent: dict[str, set[str]] = defaultdict(set)
        boundary_to_successors: dict[str, set[str]] = defaultdict(set)
        boundary_task_to_stream_key: dict[str, str] = {}
        boundary_ids: set[str] = set()
        agent_stream_key_by_id: dict[str, str] = {}
        task_ids = set()

        for task in tasks_by_id.values():
            task_id = getattr(task, "id", "") or ""
            parent_id = getattr(task, "parent_task_id", "") or ""
            if parent_id:
                children_by_parent[parent_id].add(task_id)
            task_key = task_branch_key(task)
            if task_key:
                boundary_task_to_stream_key[task_id] = task_key
            if task_key == branch_key(repo_root, branch):
                boundary_ids.add(task_id)
                task_ids.add(task_id)

        for task in tasks_by_id.values():
            boundary_task_id = (
                getattr(task, "resume_after_boundary_task_id", "") or ""
            )
            if boundary_task_id:
                boundary_to_successors[boundary_task_id].add(task.id)
                if boundary_task_id in boundary_ids:
                    task_ids.add(task.id)

        inferred_stream_agent_ids = set(stream_agent_ids or set())
        for cell in state.iter_active_agents():
            if getattr(cell, "cell_type", "") != "agent":
                continue
            if group and getattr(cell, "group", "") != group:
                continue
            identity = stream_identity_for_agent(cell)
            if not identity:
                continue
            key = branch_key(*identity)
            agent_stream_key_by_id[cell.id] = key
            if identity != (repo_root, branch):
                continue
            inferred_stream_agent_ids.add(cell.id)
            current_task_id = str(getattr(cell, "current_task_id", "") or "").strip()
            if current_task_id in tasks_by_id:
                task_ids.add(current_task_id)

        for task in tasks_by_id.values():
            if getattr(task, "agent_id", "") in inferred_stream_agent_ids:
                task_ids.add(task.id)
            if getattr(task, "reply_agent_id", "") in inferred_stream_agent_ids:
                task_ids.add(task.id)

        task_explicit_stream_keys = _build_task_explicit_stream_keys(
            tasks=tasks_by_id.values(),
            boundary_task_to_stream_key=boundary_task_to_stream_key,
            agent_stream_key_by_id=agent_stream_key_by_id,
        )
        task_descendant_stream_keys = _build_task_descendant_stream_keys(
            tasks_by_id=tasks_by_id,
            children_by_parent=children_by_parent,
            task_explicit_stream_keys=task_explicit_stream_keys,
        )
        stream_agent_ids = inferred_stream_agent_ids
        task_ids = _expand_stream_task_ids(
            seed_ids=task_ids,
            target_stream_key=branch_key(repo_root, branch),
            tasks_by_id=tasks_by_id,
            children_by_parent=children_by_parent,
            boundary_to_successors=boundary_to_successors,
            task_explicit_stream_keys=task_explicit_stream_keys,
            task_descendant_stream_keys=task_descendant_stream_keys,
        )
    stream_tasks = [
        tasks_by_id[task_id]
        for task_id in task_ids
        if task_id in tasks_by_id
    ]
    if not stream_tasks:
        return None

    classifications = {
        task.id: classify_stream_task(task, tasks_by_id)
        for task in stream_tasks
    }
    boundary_tasks = branch_boundary_tasks(
        stream_tasks,
        repo_root=repo_root,
        branch=branch,
    )
    latest_boundary = boundary_tasks[-1] if boundary_tasks else None
    review_boundary_tasks = [
        task for task in boundary_tasks
        if _is_review_task(task)
    ]
    latest_review_boundary = (
        review_boundary_tasks[-1] if review_boundary_tasks else None
    )
    reference_boundary = latest_review_boundary or latest_boundary

    queued_followers = []
    started_followers = []
    if reference_boundary:
        queued_followers = queued_successor_tasks(stream_tasks, reference_boundary.id)
        started_followers = started_successor_tasks(stream_tasks, reference_boundary.id)

    product_tasks = _sort_tasks(
        task for task in stream_tasks
        if classifications.get(task.id) == "product"
    )
    workflow_tasks = _sort_tasks(
        task for task in stream_tasks
        if classifications.get(task.id) == "workflow"
    )
    open_non_visibility_tasks = _sort_open_tasks(
        task for task in stream_tasks
        if classifications.get(task.id) != "visibility"
        and not board_task_is_closed(task)
    )
    queued_tasks = _sort_open_tasks(
        task for task in open_non_visibility_tasks
        if getattr(task, "lane", "") in _QUEUED_LANES
    )
    started_tasks = _sort_open_tasks(
        task for task in open_non_visibility_tasks
        if getattr(task, "lane", "") not in _QUEUED_LANES
    )
    review_tasks = _sort_open_tasks(
        task for task in open_non_visibility_tasks
        if _is_review_task(task)
    )
    blocker_tasks = _sort_open_tasks(
        task for task in open_non_visibility_tasks
        if _is_blocker_task(task, tasks_by_id)
    )
    validation_tasks = _sort_open_tasks(
        task for task in open_non_visibility_tasks
        if _is_validation_task(task)
    )

    merged = _is_merged_stream(boundary_tasks, state, repo_root=repo_root, branch=branch)
    latest_boundary_info = task_boundary(latest_boundary) if latest_boundary else {}
    latest_review_boundary_info = (
        task_boundary(latest_review_boundary) if latest_review_boundary else {}
    )
    readiness_boundary_info = latest_review_boundary_info or latest_boundary_info
    # Older boundaries sometimes lack the repo/branch even though the stream
    # identity is authoritative.  Supply it so the async readiness cache is
    # usable without mutating historical task evidence.
    readiness_boundary_info = {
        **readiness_boundary_info,
        "repo_root": readiness_boundary_info.get("repo_root") or repo_root,
        "branch": readiness_boundary_info.get("branch") or branch,
    }
    if not readiness_boundary_info.get("base_branch"):
        for cell in state.iter_active_agents():
            if stream_identity_for_agent(cell) != (repo_root, branch):
                continue
            base_branch = str(
                getattr(cell, "worktree_base_branch", "") or ""
            ).strip()
            if base_branch:
                readiness_boundary_info["base_branch"] = base_branch
                break
    latest_boundary_pr = boundary_pr_metadata(readiness_boundary_info)
    readiness_stale_base = _stale_base_packet(
        readiness_boundary_info, latest_boundary_pr
    )
    readiness_conflict = bool(readiness_stale_base.get("merge_conflict"))
    base_readiness_unknown = (
        readiness_stale_base.get("state") == "unknown"
        or readiness_stale_base.get("stale") is None
    )
    validation_state, validation_record = _compute_validation_state(stream_tasks)
    pending_validation = validation_state == "pending_human_validation"
    latest_reviewed_commit_sha = ""
    if latest_review_boundary:
        latest_reviewed_commit_sha = (
            task_boundary(latest_review_boundary).get("commit_sha", "") or ""
        )
    head = _current_head_packet(
        reviewed_sha=latest_reviewed_commit_sha,
        branch_advanced=False,
        boundary=readiness_boundary_info,
        pr=latest_boundary_pr,
        readiness=readiness_stale_base,
    )
    current_branch_head_sha = str(
        head.get("current_branch_head_sha", "") or ""
    ).strip()
    head_unverified = bool(
        latest_review_boundary
        and not head.get("current_branch_head_sha_verified", False)
    )
    branch_has_advanced = bool(
        latest_review_boundary
        and (
            started_followers
            or (
                current_branch_head_sha
                and current_branch_head_sha != latest_reviewed_commit_sha
            )
        )
    )
    head["branch_advanced"] = branch_has_advanced
    partial_review_safe = bool(
        latest_review_boundary
        and not branch_has_advanced
        and not head_unverified
    )
    merge_conflict = any(_is_merge_conflict_task(task) for task in blocker_tasks)

    if merged:
        code_state = "merged"
    elif merge_conflict or readiness_conflict:
        code_state = "merge_conflict"
    elif blocker_tasks:
        code_state = "review_blocked"
    elif (
        latest_review_boundary
        and not branch_has_advanced
        and not review_tasks
        and not blocker_tasks
    ):
        code_state = "reviewed_clean"
    else:
        code_state = "implementing"

    open_product_tasks = [
        task for task in open_non_visibility_tasks
        if classifications.get(task.id) == "product"
    ]

    if merged:
        stream_state = "merged"
    elif merge_conflict or readiness_conflict or blocker_tasks:
        stream_state = "fixing_blockers"
    elif review_tasks:
        stream_state = "reviewing"
    elif pending_validation and code_state == "reviewed_clean":
        stream_state = "awaiting_human_validation"
    elif (
        code_state == "reviewed_clean"
        and not open_product_tasks
        and not queued_tasks
    ):
        stream_state = (
            "merge_readiness_unknown"
            if head_unverified
            else "ready_to_merge"
        )
    else:
        stream_state = "implementing"

    # A clean review alone is not a merge-readiness proof.  We must have a
    # current-base probe before asserting ready; a failed probe is surfaced as
    # an actionable unknown rather than a confident false merge suggestion.
    if stream_state == "ready_to_merge" and base_readiness_unknown:
        stream_state = "merge_readiness_unknown"

    if merged:
        merge_state = "merged"
    elif stream_state == "ready_to_merge":
        merge_state = "ready"
    else:
        merge_state = "not_ready"

    active_blocker_task = blocker_tasks[0] if blocker_tasks else None
    active_review_task = review_tasks[0] if review_tasks else None
    blocker_parent_review_task = (
        _nearest_review_ancestor(active_blocker_task, tasks_by_id)
        if active_blocker_task
        else None
    )
    foreground_task = _select_foreground_task(
        state,
        branch=branch,
        repo_root=repo_root,
        stream_tasks=stream_tasks,
        started_tasks=started_tasks,
        blocker_task=active_blocker_task,
        review_task=active_review_task,
    )
    owner_agent = _select_owner_agent(
        state,
        stream_tasks=stream_tasks,
        repo_root=repo_root,
        branch=branch,
        stream_agent_ids=stream_agent_ids or set(),
        latest_boundary=latest_boundary,
        latest_review_boundary=latest_review_boundary,
        foreground_task=foreground_task,
    )
    active_mutable_task = _select_active_mutable_task(
        foreground_task=foreground_task,
        started_tasks=started_tasks,
    )
    visibility_items = _recent_visibility_items(
        stream_tasks,
        limit=visibility_limit,
    )

    gate_reason = _compute_gate_reason(
        stream_state=stream_state,
        blocker_task=active_blocker_task,
        review_task=active_review_task,
        validation_state=validation_state,
        validation_record=validation_record,
        merge_conflict=merge_conflict,
        validation_tasks=validation_tasks,
        branch_advanced=branch_has_advanced,
        latest_review_boundary=latest_review_boundary,
    )
    if readiness_conflict:
        gate_reason = (
            "The base branch advanced and now conflicts with this stream; "
            "rebase before merge."
        )
    elif stream_state == "merge_readiness_unknown" and head_unverified:
        gate_reason = (
            "The current branch head is unverified; do not merge until "
            "the head check succeeds."
        )
    elif stream_state == "merge_readiness_unknown":
        gate_reason = (
            "Current-base merge readiness could not be determined; "
            "do not merge until the check succeeds."
        )
    queue_gate = _compute_queue_gate(
        queue_tasks=[
            task for task in queued_tasks
            if classifications.get(task.id) == "product"
        ],
        tasks_by_id=tasks_by_id,
        blocker_task=active_blocker_task,
        review_task=active_review_task,
        validation_state=validation_state,
        validation_record=validation_record,
        validation_tasks=validation_tasks,
        code_state=code_state,
        gate_reason=gate_reason,
        merge_conflict=merge_conflict,
    )
    queue_items = _compute_queue_items(
        state,
        queue_tasks=[
            task for task in queued_tasks
            if classifications.get(task.id) == "product"
        ],
        queue_gate=queue_gate,
        review_task=active_review_task,
        active_mutable_task=active_mutable_task,
    )
    queue_counts = _queue_state_counts(queue_items)
    ready_to_resume_item = next(
        (
            item for item in queue_items
            if item.get("queue_state", "") == "ready_to_resume"
        ),
        None,
    )
    if queue_gate:
        gate_reason = str(queue_gate.get("reason", "") or "").strip() or gate_reason
    recommended_next_action = _recommended_next_action(
        stream_state=stream_state,
        validation_state=validation_state,
        validation_tasks=validation_tasks,
        merge_conflict=merge_conflict or readiness_conflict,
        has_open_product_tasks=bool(open_product_tasks or queued_tasks),
    )

    last_activity_at = _latest_activity_iso(
        stream_tasks,
        [
            state.agents.get(agent_id)
            for agent_id in {
                *(stream_agent_ids or set()),
                *{
                    getattr(task, "agent_id", "") or ""
                    for task in stream_tasks
                },
                *{
                    getattr(task, "reply_agent_id", "") or ""
                    for task in stream_tasks
                },
            }
            if agent_id and state.agents.get(agent_id)
        ],
    )

    group_value = _stream_group(stream_tasks, owner_agent)
    stream_id_value = stream_id(repo_root, branch)
    latest_merged_commit_sha = (
        str(latest_boundary_info.get("merge_commit_sha", "") or "").strip()
        or str(latest_boundary_pr.get("merge_commit_sha", "") or "").strip()
    )
    latest_review = _task_review_evidence(latest_review_boundary)
    merge_readiness = _merge_readiness_packet(
        stream_id_value=stream_id_value,
        repo_root=repo_root,
        branch=branch,
        stream_state=stream_state,
        merge_state=merge_state,
        code_state=code_state,
        product_tasks=product_tasks,
        workflow_tasks=workflow_tasks,
        queued_followers=queued_followers,
        started_followers=started_followers,
        latest_review_boundary=latest_review_boundary,
        latest_boundary_info=readiness_boundary_info,
        latest_review=latest_review,
        latest_reviewed_commit_sha=latest_reviewed_commit_sha,
        latest_merged_commit_sha=latest_merged_commit_sha,
        latest_boundary_pr=latest_boundary_pr,
        head=head,
        branch_has_advanced=branch_has_advanced,
        partial_review_safe=partial_review_safe,
        validation_state=validation_state,
        validation_record=validation_record,
        active_blocker_task=active_blocker_task,
        blocker_parent_review_task=blocker_parent_review_task,
        gate_reason=gate_reason,
        recommended_next_action=recommended_next_action,
    )
    recommended_next_action = merge_readiness.get(
        "recommended_next_action",
        recommended_next_action,
    )
    branch_exists_locally = _branch_exists_locally(
        repo_root,
        branch,
        cache=branch_exists_cache,
    )
    stream_presence = _classify_stream_presence(
        state,
        repo_root=repo_root,
        branch=branch,
        group=group,
        stream_tasks=stream_tasks,
        open_non_visibility_tasks=open_non_visibility_tasks,
        latest_boundary_status=str(
            latest_boundary_info.get("status", "") or ""
        ).strip(),
        branch_exists_locally=branch_exists_locally,
    )

    return {
        "stream_id": stream_id_value,
        "group": group_value,
        "repo_root": repo_root,
        "branch": branch,
        "stream_presence": stream_presence,
        "branch_exists_locally": branch_exists_locally,
        "agent_id": getattr(owner_agent, "id", "") or "",
        "agent_name": getattr(owner_agent, "name", "") or "",
        "agent_slug": getattr(owner_agent, "slug", "") or "",
        "foreground_task_id": getattr(foreground_task, "id", "") or "",
        "foreground_task_title": getattr(foreground_task, "task", "") or "",
        "product_task_ids": [task.id for task in product_tasks],
        "workflow_task_ids": [task.id for task in workflow_tasks],
        "queued_task_ids": [task.id for task in queued_tasks],
        "started_task_ids": [task.id for task in started_tasks],
        "recent_visibility_items": visibility_items,
        "latest_boundary_task_id": getattr(latest_boundary, "id", "") or "",
        "latest_boundary_task_title": getattr(latest_boundary, "task", "") or "",
        "latest_boundary_recorded_at": (
            latest_boundary_info.get("recorded_at", "") or ""
        ),
        "latest_boundary_status": latest_boundary_info.get("status", "") or "",
        "latest_reviewed_commit_sha": latest_reviewed_commit_sha,
        "latest_review": latest_review,
        "latest_merged_commit_sha": latest_merged_commit_sha,
        "pr": latest_boundary_pr,
        "pr_url": latest_boundary_pr.get("url", ""),
        "pr_state": latest_boundary_pr.get("state", ""),
        "pr_head_sha": latest_boundary_pr.get("head_sha", ""),
        "active_review_task_id": getattr(active_review_task, "id", "") or "",
        "active_blocker_task_id": getattr(active_blocker_task, "id", "") or "",
        "active_blocker_task_title": (
            getattr(active_blocker_task, "task", "") or ""
        ),
        "active_blocker_health_state": (
            getattr(active_blocker_task, "health_state", "") or ""
        ),
        "blocker_parent_review_task_id": (
            getattr(blocker_parent_review_task, "id", "") or ""
        ),
        "blocker_parent_review_task_title": (
            getattr(blocker_parent_review_task, "task", "") or ""
        ),
        "expected_next_transition": (
            "re-review"
            if stream_state == "fixing_blockers" or branch_has_advanced
            else ""
        ),
        "state": stream_state,
        "code_state": code_state,
        "validation_state": validation_state,
        "merge_state": merge_state,
        "queue_gate": queue_gate,
        "queue_items": queue_items,
        "queue_counts": queue_counts,
        "ready_to_resume_task_id": (
            str((ready_to_resume_item or {}).get("task_id", "") or "")
        ),
        "ready_to_resume_task_title": (
            str((ready_to_resume_item or {}).get("task_title", "") or "")
        ),
        "can_auto_resume": bool(
            ready_to_resume_item
            and (ready_to_resume_item or {}).get("deps_met", False)
        ),
        "gate_reason": gate_reason,
        "recommended_next_action": recommended_next_action,
        "recommended_tool": merge_readiness.get("recommended_tool", ""),
        "merge_readiness": merge_readiness,
        "branch_advanced": branch_has_advanced,
        "partial_review_safe": partial_review_safe,
        "last_activity_at": last_activity_at,
    }


def _classify_stream_presence(state, *, repo_root: str, branch: str,
                              group: str, stream_tasks: list[Any],
                              open_non_visibility_tasks: list[Any],
                              latest_boundary_status: str,
                              branch_exists_locally: bool) -> str:
    task_ids = {
        str(getattr(task, "id", "") or "").strip()
        for task in stream_tasks
        if getattr(task, "id", "")
    }
    stream_agents = [
        cell for cell in state.iter_active_agents()
        if getattr(cell, "cell_type", "") == "agent"
        and (not group or getattr(cell, "group", "") == group)
        and stream_identity_for_agent(cell) == (repo_root, branch)
    ]
    has_busy_agent = any(
        state.agent_is_busy(cell.id)
        or str(getattr(cell, "current_task_id", "") or "").strip() in task_ids
        for cell in stream_agents
    )

    if open_non_visibility_tasks or has_busy_agent:
        return "live"
    if branch_exists_locally or stream_agents:
        return "dormant"
    if latest_boundary_status == "open":
        return "orphaned"
    return "dormant"


def _expand_stream_task_ids(*, seed_ids: set[str], target_stream_key: str,
                            tasks_by_id: dict[str, Any],
                            children_by_parent: dict[str, set[str]],
                            boundary_to_successors: dict[str, set[str]],
                            task_explicit_stream_keys: dict[str, set[str]],
                            task_descendant_stream_keys: dict[str, set[str]]
                            ) -> set[str]:
    queue = [task_id for task_id in seed_ids if task_id in tasks_by_id]
    seen: set[str] = set()
    while queue:
        task_id = queue.pop()
        if task_id in seen or task_id not in tasks_by_id:
            continue
        seen.add(task_id)
        task = tasks_by_id[task_id]

        for related_id in (
            getattr(task, "parent_task_id", "") or "",
            getattr(task, "pipeline_root_id", "") or "",
            getattr(task, "resume_after_boundary_task_id", "") or "",
        ):
            if (
                related_id
                and related_id in tasks_by_id
                and _task_matches_stream(
                    related_id,
                    target_stream_key=target_stream_key,
                    task_explicit_stream_keys=task_explicit_stream_keys,
                    task_descendant_stream_keys=task_descendant_stream_keys,
                )
            ):
                queue.append(related_id)

        for child_id in children_by_parent.get(task_id, ()):
            if _task_matches_stream(
                child_id,
                target_stream_key=target_stream_key,
                task_explicit_stream_keys=task_explicit_stream_keys,
                task_descendant_stream_keys=task_descendant_stream_keys,
            ):
                queue.append(child_id)
        for successor_id in boundary_to_successors.get(task_id, ()):
            if _task_matches_stream(
                successor_id,
                target_stream_key=target_stream_key,
                task_explicit_stream_keys=task_explicit_stream_keys,
                task_descendant_stream_keys=task_descendant_stream_keys,
            ):
                queue.append(successor_id)
    return seen


def _root_task_id(task) -> str:
    if not task:
        return ""
    return (
        str(getattr(task, "pipeline_root_id", "") or "").strip()
        or (
            str(getattr(task, "id", "") or "").strip()
            if not getattr(task, "parent_task_id", "")
            else ""
        )
    )


def _is_visibility_task(task) -> bool:
    labels = set(getattr(task, "labels", []) or [])
    if labels & _VISIBILITY_LABELS:
        return True
    if str(getattr(task, "reply_agent_id", "") or "").strip():
        return True
    status = str(getattr(task, "status", "") or "").strip().lower()
    if status == "awaiting reply":
        return True
    for message in getattr(task, "messages", []) or []:
        if str(message.get("action", "") or "").strip() in {"engineer_message", "reply"}:
            return True
    return False


def _is_workflow_task(task, tasks_by_id: dict[str, Any]) -> bool:
    if _is_visibility_task(task):
        return False
    labels = set(getattr(task, "labels", []) or [])
    if labels & _WORKFLOW_LABELS:
        return True
    if getattr(task, "parent_task_id", "") or getattr(task, "pipeline_depth", 0):
        return True
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    if action_name:
        if action_name in _PRODUCT_ACTIONS:
            return False
        if any(hint in action_name for hint in _NON_PRODUCT_ACTION_HINTS):
            return True
    text = _task_text(task)
    if any(hint in text for hint in _REVIEW_HINTS + _VALIDATION_HINTS):
        return True
    if _is_merge_conflict_task(task):
        return True
    parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
    parent = tasks_by_id.get(parent_id) if parent_id else None
    if parent and _is_review_task(parent):
        return True
    return False


def _is_review_task(task) -> bool:
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    labels = {
        str(label or "").strip().lower()
        for label in (getattr(task, "labels", []) or [])
    }
    if action_name.endswith("fix-review") or "review-fix" in labels:
        return False
    if "review" in action_name:
        return True
    status = str(getattr(task, "status", "") or "").strip().lower()
    if status == "on review":
        return True
    text = _task_text(task)
    return any(hint in text for hint in _REVIEW_HINTS)


def _is_validation_task(task) -> bool:
    if _is_visibility_task(task):
        return False
    action_name = str(getattr(task, "action_name", "") or "").strip().lower()
    if "validate" in action_name or "validation" in action_name:
        return True
    text = _task_text(task)
    return any(hint in text for hint in _VALIDATION_HINTS)


def _is_merge_conflict_task(task) -> bool:
    if _is_visibility_task(task):
        return False
    text = _task_text(task)
    return any(hint in text for hint in _MERGE_CONFLICT_HINTS)


def _is_blocker_task(task, tasks_by_id: dict[str, Any]) -> bool:
    if _is_visibility_task(task) or _is_review_task(task) or _is_validation_task(task):
        return False
    if _is_merge_conflict_task(task):
        return True
    parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
    if parent_id and _has_review_ancestor(parent_id, tasks_by_id):
        return True
    status = str(getattr(task, "status", "") or "").strip().lower()
    if status == "fixing":
        return True
    root_id = _root_task_id(task)
    root_task = tasks_by_id.get(root_id) if root_id else None
    if root_task:
        root_status = str(getattr(root_task, "status", "") or "").strip().lower()
        if root_status == "fixing":
            return True
    return False


def _has_review_ancestor(task_id: str, tasks_by_id: dict[str, Any]) -> bool:
    current_id = task_id
    while current_id:
        current = tasks_by_id.get(current_id)
        if not current:
            return False
        if _is_review_task(current):
            return True
        current_id = str(getattr(current, "parent_task_id", "") or "").strip()
    return False


def _select_foreground_task(state, *, branch: str, repo_root: str,
                            stream_tasks: list[Any], started_tasks: list[Any],
                            blocker_task=None, review_task=None):
    if blocker_task and getattr(blocker_task, "lane", "") not in _QUEUED_LANES:
        return blocker_task
    if review_task and getattr(review_task, "lane", "") not in _QUEUED_LANES:
        return review_task

    for cell in state.iter_active_agents():
        if getattr(cell, "cell_type", "") != "agent":
            continue
        identity = stream_identity_for_agent(cell)
        if identity != (repo_root, branch):
            continue
        current = state.agent_current_task(cell.id)
        if current and current in stream_tasks and not _is_visibility_task(current):
            return current

    return started_tasks[0] if started_tasks else None


def _select_owner_agent(state, *, stream_tasks: list[Any], repo_root: str,
                        branch: str, stream_agent_ids: set[str],
                        latest_boundary=None, latest_review_boundary=None,
                        foreground_task=None):
    candidate_ids = set(stream_agent_ids or set())
    for task in stream_tasks:
        for agent_id in (
            str(getattr(task, "agent_id", "") or "").strip(),
            str(getattr(task, "reply_agent_id", "") or "").strip(),
            str(task_boundary(task).get("recorded_by_agent_id", "") or "").strip(),
        ):
            if agent_id:
                candidate_ids.add(agent_id)

    candidates = [
        state.agents.get(agent_id)
        for agent_id in candidate_ids
        if agent_id and state.agents.get(agent_id)
        and getattr(state.agents.get(agent_id), "cell_type", "") == "agent"
    ]
    if not candidates:
        return None

    stream_task_ids = {task.id for task in stream_tasks}
    latest_mutable_boundary = _latest_mutable_boundary_task(
        stream_tasks,
        latest_boundary=latest_boundary,
        latest_review_boundary=latest_review_boundary,
    )
    preferred_current_owner_id = _preferred_owner_agent_id(
        stream_tasks,
        latest_mutable_boundary=latest_mutable_boundary,
        foreground_task=foreground_task,
    )
    mutable_task_ids = {
        task.id for task in stream_tasks
        if _is_mutable_task(task)
    }

    def _sort_key(cell):
        identity = stream_identity_for_agent(cell)
        current = state.agent_current_task(cell.id)
        current_id = getattr(current, "id", "") if current else ""
        current_mutable = bool(current_id and current_id in mutable_task_ids)
        linked_mutable = any(
            str(getattr(task, "agent_id", "") or "").strip() == cell.id
            and _is_mutable_task(task)
            for task in stream_tasks
        )
        boundary_owner = bool(
            latest_mutable_boundary
            and str(
                task_boundary(latest_mutable_boundary).get("recorded_by_agent_id", "")
                or ""
            ).strip() == cell.id
        )
        return (
            0 if cell.id == preferred_current_owner_id else 1,
            0 if boundary_owner else 1,
            0 if current_mutable else 1,
            0 if linked_mutable else 1,
            0 if identity == (repo_root, branch) else 1,
            0 if current_id in stream_task_ids else 1,
            0 if getattr(cell, "status", "") == "running" else 1,
            # agent_is_busy(cell.id) ≡ current is not None, which
            # current_mutable already implies — no need to rescan the board.
            0 if current_mutable else 1,
            -float(
                getattr(cell, "last_activity_at", 0.0)
                or getattr(cell, "last_event_at", 0.0)
                or 0.0
            ),
            str(getattr(cell, "name", "") or "").lower(),
            cell.id,
        )

    candidates.sort(key=_sort_key)
    return candidates[0]


def _select_active_mutable_task(*, foreground_task, started_tasks: list[Any]):
    if (
        foreground_task
        and _is_mutable_task(foreground_task)
        and getattr(foreground_task, "lane", "") not in _QUEUED_LANES
    ):
        return foreground_task
    for task in started_tasks:
        if _is_mutable_task(task):
            return task
    return None


def _build_task_explicit_stream_keys(*, tasks, boundary_task_to_stream_key: dict[str, str],
                                     agent_stream_key_by_id: dict[str, str]
                                     ) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for task in tasks:
        task_id = getattr(task, "id", "") or ""
        if not task_id:
            continue
        keys = set()
        direct_key = task_branch_key(task)
        if direct_key:
            keys.add(direct_key)
        resume_after = str(getattr(task, "resume_after_boundary_task_id", "") or "").strip()
        if resume_after and resume_after in boundary_task_to_stream_key:
            keys.add(boundary_task_to_stream_key[resume_after])
        for agent_id in (
            str(getattr(task, "agent_id", "") or "").strip(),
            str(getattr(task, "reply_agent_id", "") or "").strip(),
        ):
            key = agent_stream_key_by_id.get(agent_id, "")
            if key:
                keys.add(key)
        out[task_id] = keys
    return out


def _build_task_descendant_stream_keys(*, tasks_by_id: dict[str, Any],
                                       children_by_parent: dict[str, set[str]],
                                       task_explicit_stream_keys: dict[str, set[str]]
                                       ) -> dict[str, set[str]]:
    cache: dict[str, set[str]] = {}

    def _collect(task_id: str) -> set[str]:
        if task_id in cache:
            return set(cache[task_id])
        keys = set(task_explicit_stream_keys.get(task_id, set()))
        for child_id in children_by_parent.get(task_id, ()):
            if child_id not in tasks_by_id:
                continue
            keys.update(_collect(child_id))
        cache[task_id] = set(keys)
        return set(keys)

    for task_id in tasks_by_id:
        _collect(task_id)
    return cache


def _task_matches_stream(task_id: str, *, target_stream_key: str,
                         task_explicit_stream_keys: dict[str, set[str]],
                         task_descendant_stream_keys: dict[str, set[str]]) -> bool:
    explicit_keys = task_explicit_stream_keys.get(task_id, set())
    if explicit_keys:
        return target_stream_key in explicit_keys
    descendant_keys = task_descendant_stream_keys.get(task_id, set())
    if descendant_keys:
        return target_stream_key in descendant_keys
    return True


def _latest_mutable_boundary_task(stream_tasks: list[Any], *,
                                  latest_boundary=None, latest_review_boundary=None):
    if latest_boundary and _is_mutable_task(latest_boundary):
        return latest_boundary
    if latest_review_boundary and latest_review_boundary in stream_tasks:
        prior = [
            task for task in stream_tasks
            if task_boundary(task)
            and _is_mutable_task(task)
            and _task_activity_ts(task) <= _task_activity_ts(latest_review_boundary)
        ]
        prior = _sort_tasks(prior)
        if prior:
            return prior[-1]
    boundaries = _sort_tasks(
        task for task in stream_tasks
        if task_boundary(task) and _is_mutable_task(task)
    )
    return boundaries[-1] if boundaries else None


def _preferred_owner_agent_id(stream_tasks: list[Any], *, latest_mutable_boundary=None,
                              foreground_task=None) -> str:
    if foreground_task and _is_mutable_task(foreground_task):
        agent_id = str(getattr(foreground_task, "agent_id", "") or "").strip()
        if agent_id:
            return agent_id
    if latest_mutable_boundary:
        boundary_agent_id = str(
            task_boundary(latest_mutable_boundary).get("recorded_by_agent_id", "") or ""
        ).strip()
        if boundary_agent_id:
            return boundary_agent_id
    mutable_tasks = _sort_open_tasks(
        task for task in stream_tasks
        if _is_mutable_task(task)
    )
    for task in mutable_tasks:
        agent_id = str(getattr(task, "agent_id", "") or "").strip()
        if agent_id:
            return agent_id
    historical_tasks = _sort_tasks(
        task for task in stream_tasks
        if _is_mutable_task(task)
    )
    for task in reversed(historical_tasks):
        agent_id = str(getattr(task, "agent_id", "") or "").strip()
        if agent_id:
            return agent_id
    return ""


def _is_mutable_task(task) -> bool:
    return (
        bool(task)
        and not _is_visibility_task(task)
        and not _is_review_task(task)
        and not _is_validation_task(task)
    )


def _is_held_task(task) -> bool:
    labels = {
        str(label or "").strip().lower()
        for label in (getattr(task, "labels", []) or [])
    }
    return bool(labels & _HELD_LABELS)


def _nearest_review_ancestor_id(task, tasks_by_id: dict[str, Any]) -> str:
    review = _nearest_review_ancestor(task, tasks_by_id)
    return str(getattr(review, "id", "") or "").strip() if review else ""


def _nearest_review_ancestor(task, tasks_by_id: dict[str, Any]):
    current = task
    while current:
        current_id = str(getattr(current, "id", "") or "").strip()
        if current_id and _is_review_task(current):
            return current
        parent_id = str(getattr(current, "parent_task_id", "") or "").strip()
        current = tasks_by_id.get(parent_id) if parent_id else None
    return None


def _compute_queue_gate(*, queue_tasks: list[Any], tasks_by_id: dict[str, Any],
                        blocker_task, review_task, validation_state: str,
                        validation_record: dict, validation_tasks: list[Any],
                        code_state: str, gate_reason: str,
                        merge_conflict: bool) -> dict:
    reason = str(gate_reason or "").strip()
    if merge_conflict and blocker_task:
        return {
            "gate_type": "merge_conflict",
            "blocking_task_id": getattr(blocker_task, "id", "") or "",
            "source_task_id": (
                _nearest_review_ancestor_id(blocker_task, tasks_by_id)
                or getattr(review_task, "id", "") or ""
            ),
            "reason": reason or getattr(blocker_task, "task", "") or (
                "Merge conflict must be resolved before resuming queued work"
            ),
            "clears_when": "conflict_resolved",
        }
    if blocker_task:
        return {
            "gate_type": "review_blocker",
            "blocking_task_id": getattr(blocker_task, "id", "") or "",
            "source_task_id": (
                _nearest_review_ancestor_id(blocker_task, tasks_by_id)
                or getattr(review_task, "id", "") or ""
            ),
            "reason": reason or getattr(blocker_task, "task", "") or (
                "Review blockers must be fixed before queued work resumes"
            ),
            "clears_when": "review_passes",
        }
    if validation_state == "pending_human_validation" and code_state == "reviewed_clean":
        blocking_task_id = ""
        if validation_tasks:
            blocking_task_id = getattr(validation_tasks[0], "id", "") or ""
        elif validation_record:
            blocking_task_id = str(validation_record.get("task_id", "") or "").strip()
        return {
            "gate_type": "human_validation",
            "blocking_task_id": blocking_task_id,
            "source_task_id": str(validation_record.get("task_id", "") or "").strip(),
            "reason": reason or "Human validation must complete before queued work resumes",
            "clears_when": "human_validation_cleared",
        }
    held_task = (
        queue_tasks[0]
        if queue_tasks and _is_held_task(queue_tasks[0])
        else None
    )
    if held_task:
        return {
            "gate_type": "manual_hold",
            "blocking_task_id": getattr(held_task, "id", "") or "",
            "source_task_id": getattr(held_task, "id", "") or "",
            "reason": getattr(held_task, "task", "") or "Queued work is on hold",
            "clears_when": "manual_hold_released",
        }
    return {}


def _compute_queue_items(state, *, queue_tasks: list[Any], queue_gate: dict,
                         review_task, active_mutable_task) -> list[dict]:
    items = []
    gate_type = str((queue_gate or {}).get("gate_type", "") or "").strip()
    gate_blocks_with_blocker = gate_type in {"review_blocker", "merge_conflict"}
    gate_blocks_with_validation = gate_type == "human_validation"
    held_task_id = str((queue_gate or {}).get("blocking_task_id", "") or "").strip()

    for index, task in enumerate(queue_tasks):
        queue_state = "queued"
        deps_met = bool(state.board_deps_met(task))
        if gate_blocks_with_blocker:
            queue_state = "paused_by_blocker"
        elif gate_blocks_with_validation:
            queue_state = "paused_by_validation"
        elif gate_type == "manual_hold" and getattr(task, "id", "") == held_task_id:
            queue_state = "held"
        elif review_task:
            queue_state = "paused_by_review"
        elif active_mutable_task:
            queue_state = "queued"
        elif index == 0 and deps_met:
            queue_state = "ready_to_resume"

        items.append({
            "task_id": getattr(task, "id", "") or "",
            "task_title": getattr(task, "task", "") or "",
            "lane": getattr(task, "lane", "") or "",
            "queue_state": queue_state,
            "deps_met": deps_met,
            "resume_after_boundary_task_id": (
                getattr(task, "resume_after_boundary_task_id", "") or ""
            ),
            "held": _is_held_task(task),
        })
    return items


def _queue_state_counts(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        state_name = str((item or {}).get("queue_state", "") or "").strip()
        if state_name:
            counts[state_name] += 1
    return dict(sorted(counts.items()))


def _compute_validation_state(tasks: list[Any]) -> tuple[str, dict]:
    latest = None
    latest_ts = -1.0

    for task in tasks:
        summary = getattr(task, "verification_summary", {}) or {}
        if not isinstance(summary, dict):
            summary = {}
        has_signal = bool(
            getattr(task, "verification_state", "")
            or getattr(task, "verification_notes", "")
            or summary
        )
        if not has_signal:
            continue
        ts = _parse_iso(getattr(task, "verification_updated_at", "") or "")
        if not ts:
            ts = _task_activity_ts(task)
        if ts >= latest_ts:
            latest_ts = ts
            latest = {
                "task_id": getattr(task, "id", "") or "",
                "verification_state": (
                    getattr(task, "verification_state", "") or ""
                ),
                "verification_notes": (
                    getattr(task, "verification_notes", "") or ""
                ),
                "summary": dict(summary),
            }

    if not latest:
        return "none", {}

    state = str(latest.get("verification_state", "") or "").strip()
    summary = latest.get("summary", {}) or {}
    notes = str(latest.get("verification_notes", "") or "").strip()
    pending_text = str(summary.get("human_validation_pending", "") or "").strip()
    combined = " ".join(part for part in (pending_text, notes) if part).lower()

    if "waiv" in combined:
        return "waived", latest
    if state == "failed":
        if pending_text or summary.get("manual_smoke_done"):
            return "pending_human_validation", latest
        return "automated_only", latest
    if state == "passed" or summary.get("manual_smoke_done"):
        return "validated", latest
    if state == "pending" or pending_text:
        return "pending_human_validation", latest
    if state == "attempted" or summary.get("tests_run") or summary.get("deploy_attempted"):
        return "automated_only", latest
    return "none", latest


def _compute_gate_reason(*, stream_state: str, blocker_task, review_task,
                         validation_state: str, validation_record: dict,
                         merge_conflict: bool, validation_tasks: list[Any],
                         branch_advanced: bool, latest_review_boundary) -> str:
    if stream_state == "merged":
        return ""
    if merge_conflict and blocker_task:
        return getattr(blocker_task, "task", "") or "Merge conflict must be resolved"
    if blocker_task:
        return getattr(blocker_task, "task", "") or "Review blockers must be fixed"
    if review_task:
        return getattr(review_task, "task", "") or "Review is in progress"
    if validation_state == "pending_human_validation":
        summary = (validation_record or {}).get("summary", {}) or {}
        pending_text = str(summary.get("human_validation_pending", "") or "").strip()
        if pending_text:
            return pending_text
        notes = str((validation_record or {}).get("verification_notes", "") or "").strip()
        if notes:
            return notes
        if validation_tasks:
            return getattr(validation_tasks[0], "task", "") or "Manual validation pending"
        return "Human or runtime validation is still pending"
    if branch_advanced and latest_review_boundary:
        return (
            "The branch has advanced beyond the latest reviewed boundary; "
            "the reviewed commit is no longer current."
        )
    return ""


def _recommended_next_action(*, stream_state: str, validation_state: str,
                             validation_tasks: list[Any], merge_conflict: bool,
                             has_open_product_tasks: bool) -> str:
    if stream_state == "merged":
        return "none"
    if stream_state == "merge_readiness_unknown":
        return "check_merge_readiness"
    if merge_conflict:
        return "resolve_merge_conflict"
    if stream_state == "fixing_blockers":
        return "address_review_blockers"
    if stream_state == "reviewing":
        return "wait_for_review"
    if validation_state == "pending_human_validation":
        return "run_manual_validation" if validation_tasks else "merge_after_validation"
    if stream_state == "ready_to_merge":
        return "merge"
    if has_open_product_tasks:
        return "continue_implementation"
    return "continue_implementation"


def _recent_visibility_items(tasks: list[Any], *, limit: int = 10) -> list[dict]:
    items = []
    for task in tasks:
        if not _is_visibility_task(task):
            continue
        task_items = []
        for message in getattr(task, "messages", []) or []:
            action = str(message.get("action", "") or "").strip()
            if action not in {"engineer_message", "reply"}:
                continue
            kind = "agent_reply" if action == "reply" else "engineer_message"
            summary = _summarize_visibility_message(
                str(message.get("message", "") or "").strip()
            )
            task_items.append({
                "kind": kind,
                "task_id": getattr(task, "id", "") or "",
                "task_title": getattr(task, "task", "") or "",
                "summary": summary,
                "timestamp": _timestamp_to_iso(message.get("timestamp")),
                "_ts": _numeric_timestamp(message.get("timestamp")),
            })

        if not task_items:
            created = _parse_iso(getattr(task, "created_at", "") or "")
            task_items.append({
                "kind": "engineer_message",
                "task_id": getattr(task, "id", "") or "",
                "task_title": getattr(task, "task", "") or "",
                "summary": _summarize_visibility_message(
                    str(getattr(task, "description", "") or getattr(task, "task", "") or "")
                ),
                "timestamp": _timestamp_to_iso(created),
                "_ts": created,
            })

        items.extend(task_items)

    items.sort(
        key=lambda item: (
            -(item.get("_ts", 0.0) or 0.0),
            item.get("task_id", ""),
            item.get("kind", ""),
        )
    )
    trimmed = items[:max(0, int(limit))]
    for item in trimmed:
        item.pop("_ts", None)
    return trimmed


def _summarize_visibility_message(message: str, *, limit: int = 72) -> str:
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    summary = lines[0] if lines else str(message or "").strip()
    if not summary:
        return "Communication"
    if len(summary) <= limit:
        return summary
    return summary[:limit - 1].rstrip() + "…"


def _is_merged_stream(boundary_tasks: list[Any], state, *,
                      repo_root: str, branch: str) -> bool:
    if boundary_tasks:
        latest = task_boundary(boundary_tasks[-1])
        if latest.get("status", "") == "merged":
            return True
    for cell in state.iter_active_agents():
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if stream_identity_for_agent(cell) != (repo_root, branch):
            continue
        if bool(getattr(cell, "worktree_merged", False)):
            return True
    return False


def _stream_group(stream_tasks: list[Any], owner_agent) -> str:
    if owner_agent and getattr(owner_agent, "group", ""):
        return owner_agent.group
    counts: dict[str, int] = defaultdict(int)
    for task in stream_tasks:
        group = str(getattr(task, "group", "") or "").strip()
        if group:
            counts[group] += 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _sort_tasks(tasks) -> list[Any]:
    ordered = list(tasks)
    ordered.sort(
        key=lambda task: (
            _task_canonical_order(task),
            getattr(task, "created_at", "") or "",
            getattr(task, "id", "") or "",
        )
    )
    return ordered


def _sort_open_tasks(tasks) -> list[Any]:
    ordered = list(tasks)
    ordered.sort(
        key=lambda task: (
            _lane_priority(getattr(task, "lane", "")),
            -_task_activity_ts(task),
            _task_canonical_order(task),
            getattr(task, "id", "") or "",
        )
    )
    return ordered


def _task_canonical_order(task) -> tuple[int, int, str]:
    parsed = parse_task_id(getattr(task, "id", "") or "")
    if not parsed:
        return (10**9, 10**9, getattr(task, "id", "") or "")
    child = parsed.get("child_number")
    return (
        int(parsed.get("root_number", 10**9)),
        int(child if child is not None else 0),
        getattr(task, "id", "") or "",
    )


def _lane_priority(lane: str) -> int:
    if lane == "In Progress":
        return 0
    if lane == "To Do":
        return 1
    if lane == "Backlog":
        return 2
    return 3


def _task_text(task) -> str:
    return " ".join(
        part.strip().lower()
        for part in (
            str(getattr(task, "task", "") or ""),
            str(getattr(task, "description", "") or ""),
            str(getattr(task, "action_name", "") or ""),
            str(getattr(task, "status", "") or ""),
        )
        if part and part.strip()
    )


def _task_activity_ts(task) -> float:
    timestamps = [
        _parse_iso(getattr(task, "updated_at", "") or ""),
        _parse_iso(getattr(task, "created_at", "") or ""),
    ]
    for message in getattr(task, "messages", []) or []:
        timestamps.append(_numeric_timestamp(message.get("timestamp")))
    timestamps = [ts for ts in timestamps if ts]
    return max(timestamps) if timestamps else 0.0


def _latest_activity_iso(tasks: list[Any], agents: list[Any]) -> str:
    latest = 0.0
    for task in tasks:
        latest = max(latest, _task_activity_ts(task))
    for cell in agents:
        latest = max(
            latest,
            float(
                getattr(cell, "last_activity_at", 0.0)
                or getattr(cell, "last_event_at", 0.0)
                or 0.0
            ),
        )
    return _timestamp_to_iso(latest)


def _parse_iso(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _numeric_timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return _parse_iso(value)
    return 0.0


def _timestamp_to_iso(value) -> str:
    ts = _numeric_timestamp(value)
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
