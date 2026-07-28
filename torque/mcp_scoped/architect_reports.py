"""Architect board, attention, wave-summary, and completion-audit reports."""

import copy
import json

from torque.config import log
from torque.mcp_engineer_tools.shared import resolve_task as _resolve_task
from torque.mcp_scoped.common import (
    _agent_peer_message_row_to_entry,
    _architect_hired_engineer_ids,
    _attach_task_board_sync_inline_state,
    _attach_task_review_inline_state,
    _dedupe_strings,
    _effective_assigned_engineer_id,
    _filter_tasks_for_caller,
    _load_same_group_architect_decision,
    _peer_row_context,
    _summary_task_title,
    _task_board_sync_inline_state,
    _task_created_by_classifier,
    _thread_requires_architect_reply,
)
from torque.state import (
    ARCHIVED_LANE,
    board_task_is_closed,
    task_counts_as_done,
    task_is_engineer_message_followup,
)
from torque.task_health import HEALTH_SEVERITY
from torque.worktree_streams import (
    compute_worktree_streams,
    member_task_ids_for_stream,
)

_ARCHITECT_BOARD_SUMMARY_TASK_LIMIT = 20
_ARCHITECT_BOARD_SUMMARY_RESPONSE_LIMIT = 10_000
_ARCHITECT_ATTENTION_DEFAULT_LIMIT = 5
_ARCHITECT_ATTENTION_MAX_LIMIT = 20
_ARCHITECT_WAVE_SUMMARY_DEFAULT_LIMIT = 8
_ARCHITECT_WAVE_SUMMARY_MAX_LIMIT = 20
_ARCHITECT_WAVE_SUMMARY_RESPONSE_LIMIT = 12_000
_ARCHITECT_COMPLETION_AUDIT_DEFAULT_LIMIT = 8
_ARCHITECT_COMPLETION_AUDIT_MAX_LIMIT = 20
_ARCHITECT_COMPLETION_AUDIT_RESPONSE_LIMIT = 12_000
_ARCHITECT_TASK_LIST_DEFAULT_LIMIT = 100
_ARCHITECT_PEER_SUMMARY_LOAD_LIMIT = 1000

def _board_sync_summary_payload(tasks) -> dict:
    items = []
    for task in tasks:
        board_sync = _task_board_sync_inline_state(task)
        if not board_sync:
            continue
        items.append({
            "id": getattr(task, "id", "") or "",
            "title": _summary_task_title(task),
            **board_sync,
        })
    if not items:
        return {}
    error_count = sum(1 for item in items if item.get("sync_state") == "error")
    return {
        "count": len(items),
        "error_count": error_count,
        "items": items[:10],
        "truncated": len(items) > 10,
    }


def _architect_board_summary_task_item(task, *, created_by: str) -> dict:
    item = {
        "id": task.id,
        "slug": task.slug,
        "title": _summary_task_title(task),
        "lane": task.lane,
        "labels": task.labels or [],
        "status": task.status,
        "dispatch_state": getattr(task, "dispatch_state", "queued") or "queued",
        "assigned_engineer_id": _effective_assigned_engineer_id(task),
        "created_by": created_by,
        "health_state": getattr(task, "health_state", "healthy") or "healthy",
        "updated_at": getattr(task, "updated_at", "") or "",
    }
    assigned_architect_id = str(
        getattr(task, "assigned_architect_id", "") or ""
    ).strip()
    if assigned_architect_id:
        item["assigned_architect_id"] = assigned_architect_id
    suggested_specialization = str(
        getattr(task, "suggested_specialization", "") or ""
    ).strip()
    if suggested_specialization:
        item["suggested_specialization"] = suggested_specialization
    _attach_task_board_sync_inline_state(item, task)
    _attach_task_review_inline_state(item, task)
    return item


def _normalize_architect_task_list_label_filter(value) -> tuple[list[str], str]:
    if value in (None, ""):
        return [], ""
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, list):
        raw_values = value
    else:
        return [], "label_filter must be a string or list of strings"

    labels = []
    seen = set()
    for item in raw_values:
        if not isinstance(item, str):
            return [], "label_filter entries must be strings"
        label = item.strip()
        if not label or label in seen:
            continue
        labels.append(label)
        seen.add(label)
    return labels, ""


async def _validate_task_update_action_name(
        action_name: str,
        group: str,
        handle_command) -> str:
    """Return an error message if an architect action binding is invalid."""
    name = str(action_name or "").strip()
    if not name:
        return ""
    result = await handle_command({
        "cmd": "list_actions",
        "group": str(group or "").strip(),
    })
    if isinstance(result, dict) and result.get("type") == "error":
        return (
            "Unable to validate action_name via ActionManager.list_actions(): "
            f"{result.get('message', 'Unknown error')}"
        )
    actions = result.get("actions") if isinstance(result, dict) else None
    if not isinstance(actions, list):
        return (
            "Unable to validate action_name via ActionManager.list_actions(): "
            "list_actions returned an unexpected response"
        )
    action_names = {
        str(item.get("name", "") if isinstance(item, dict) else item).strip()
        for item in actions
        if str(item.get("name", "") if isinstance(item, dict) else item).strip()
    }
    if name not in action_names:
        return (
            f"Unknown action_name '{name}' "
            "(validated against ActionManager.list_actions() "
            f"for group '{str(group or '').strip()}')"
        )
    return ""


def _normalize_architect_task_list_limit(value) -> tuple[int, str]:
    if value in (None, ""):
        return _ARCHITECT_TASK_LIST_DEFAULT_LIMIT, ""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 0, "limit must be an integer"
    if limit < 0:
        return 0, "limit must be at least 0"
    return limit, ""


def _normalize_architect_attention_limit(value) -> tuple[int, str]:
    if value in (None, ""):
        return _ARCHITECT_ATTENTION_DEFAULT_LIMIT, ""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 0, "limit_per_section must be an integer"
    if limit < 1:
        return 0, "limit_per_section must be at least 1"
    return min(limit, _ARCHITECT_ATTENTION_MAX_LIMIT), ""


def _bounded_items(items: list[dict], limit: int) -> dict:
    return {
        "count": len(items),
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


def _architect_attention_task_item(task, *, created_by: str = "") -> dict:
    item = {
        "id": getattr(task, "id", "") or "",
        "title": _summary_task_title(task),
        "lane": getattr(task, "lane", "") or "",
        "status": getattr(task, "status", "") or "",
        "assigned_engineer_id": _effective_assigned_engineer_id(task),
        "assigned_architect_id": str(getattr(task, "assigned_architect_id", "") or "").strip(),
        "agent_id": str(getattr(task, "agent_id", "") or "").strip(),
        "updated_at": getattr(task, "updated_at", "") or "",
    }
    if created_by:
        item["created_by"] = created_by
    health_state = str(getattr(task, "health_state", "") or "").strip()
    if health_state and health_state != "healthy":
        item["health_state"] = health_state
        item["health_since"] = getattr(task, "health_since", "") or ""
    return {key: value for key, value in item.items() if value not in ("", None)}


def _task_parked_or_deferred(task) -> bool:
    labels = {
        str(label or "").strip().lower()
        for label in (getattr(task, "labels", []) or [])
    }
    if labels.intersection({"deferred", "torque:hold", "hold", "parked"}):
        return True
    status = str(getattr(task, "status", "") or "").strip().lower()
    return status in {"deferred", "parked", "on hold", "hold"}


def _stream_stale_base(stream: dict) -> dict:
    readiness = (stream or {}).get("merge_readiness", {})
    if not isinstance(readiness, dict):
        readiness = {}
    stale = readiness.get("stale_base", {})
    if not isinstance(stale, dict):
        stale = {}
    return stale


def _stream_recommended_next_action(stream: dict) -> str:
    readiness = (stream or {}).get("merge_readiness", {})
    if isinstance(readiness, dict):
        action = str(readiness.get("recommended_next_action", "") or "").strip()
        if action:
            return action
    return str((stream or {}).get("recommended_next_action", "") or "").strip()


def _architect_attention_stream_item(stream: dict) -> dict:
    item = {
        "stream_id": str((stream or {}).get("stream_id", "") or ""),
        "state": str((stream or {}).get("state", "") or ""),
        "branch": str((stream or {}).get("branch", "") or ""),
        "repo_root": str((stream or {}).get("repo_root", "") or ""),
        "agent_id": str((stream or {}).get("agent_id", "") or ""),
        "agent_name": str((stream or {}).get("agent_name", "") or ""),
        "foreground_task_id": str(
            (stream or {}).get("foreground_task_id", "") or ""
        ),
        "foreground_task_title": str(
            (stream or {}).get("foreground_task_title", "") or ""
        ),
        "active_blocker_task_id": str(
            (stream or {}).get("active_blocker_task_id", "") or ""
        ),
        "active_blocker_task_title": str(
            (stream or {}).get("active_blocker_task_title", "") or ""
        ),
        "product_task_ids": list((stream or {}).get("product_task_ids", []) or []),
        "workflow_task_ids": list((stream or {}).get("workflow_task_ids", []) or []),
        "pr_url": str((stream or {}).get("pr_url", "") or ""),
        "branch_advanced": bool((stream or {}).get("branch_advanced", False)),
        "recommended_next_action": _stream_recommended_next_action(stream),
        "last_activity_at": str((stream or {}).get("last_activity_at", "") or ""),
    }
    stale = _stream_stale_base(stream)
    if stale:
        item["stale_base"] = stale
    return {
        key: value for key, value in item.items()
        if value not in ("", None, {}, [])
    }


def _stream_owner_engineer_ids(state, stream: dict) -> set[str]:
    owner_ids: set[str] = set()
    agent_id = str((stream or {}).get("agent_id", "") or "").strip()
    agent = state.agents.get(agent_id)
    if agent:
        for field in ("owner_engineer_id", "created_by_engineer_id"):
            value = str(getattr(agent, field, "") or "").strip()
            if value:
                owner_ids.add(value)
        if str(getattr(agent, "kind", "") or "").strip() == "engineer":
            owner_ids.add(str(getattr(agent, "id", "") or "").strip())
    for task_id in member_task_ids_for_stream(stream):
        task = state.board_tasks.get(task_id)
        if not task:
            continue
        for value in (
            _effective_assigned_engineer_id(task),
            str(getattr(task, "created_by_engineer_id", "") or "").strip(),
        ):
            if value:
                owner_ids.add(value)
    return {owner_id for owner_id in owner_ids if owner_id}


def _stream_owned_by_hired_engineer(state, stream: dict,
                                    hired_engineer_ids: set[str]) -> bool:
    return bool(_stream_owner_engineer_ids(state, stream) & hired_engineer_ids)


def _stream_unhealthy_task_items(state, stream: dict) -> list[dict]:
    items = []
    for task_id in member_task_ids_for_stream(stream):
        task = state.board_tasks.get(task_id)
        if not task or board_task_is_closed(task):
            continue
        if _task_parked_or_deferred(task):
            continue
        health_state = str(
            getattr(task, "health_state", "") or "healthy"
        ).strip() or "healthy"
        if health_state == "healthy":
            continue
        items.append(_architect_attention_task_item(
            task,
            created_by=_task_created_by_classifier(task),
        ))
    items.sort(
        key=lambda item: (
            -HEALTH_SEVERITY.get(item.get("health_state", ""), 0),
            item.get("health_since", ""),
            item.get("title", "").lower(),
        )
    )
    return items


def _architect_peer_ack_candidates(state, caller_id: str) -> dict:
    if not getattr(state, "db", None):
        return {
            "count": 0,
            "items": [],
            "source_truncated": False,
            "load_limit": _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
        }
    try:
        rows = state.db.load_agent_peer_messages_for_agent(
            caller_id,
            limit=_ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
        )
    except Exception as exc:
        log.exception("Failed to load Architect peer ack attention rows")
        return {
            "count": 0,
            "items": [],
            "source_truncated": False,
            "load_limit": _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
            "error": str(exc),
        }
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        thread_id = str((row or {}).get("thread_id", "") or "").strip()
        if not thread_id:
            continue
        grouped.setdefault(thread_id, []).append(row)
    threads = []
    for thread_id, messages in grouped.items():
        messages.sort(
            key=lambda row: (
                float(row.get("created_at", 0) or 0),
                str(row.get("id", "") or ""),
            )
        )
        if not _thread_requires_architect_reply(messages, caller_id):
            continue
        last = messages[-1]
        entry_for_caller = _agent_peer_message_row_to_entry(last, caller_id)
        peer_architect_id = entry_for_caller["peer_id"]
        peer = state.agents.get(peer_architect_id)
        threads.append({
            "thread_id": thread_id,
            "peer_architect_id": peer_architect_id,
            "peer_name": str(getattr(peer, "name", "") or "").strip()
            or peer_architect_id,
            "last_message_at": float(last.get("created_at", 0) or 0),
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
    items = []
    for thread in threads if isinstance(threads, list) else []:
        messages = thread.get("messages", []) if isinstance(thread, dict) else []
        latest_ack = {}
        for message in messages if isinstance(messages, list) else []:
            if (
                isinstance(message, dict)
                and message.get("direction") == "received"
                and message.get("ack_required")
            ):
                latest_ack = message
        item = {
            "thread_id": str(thread.get("thread_id", "") or ""),
            "peer_architect_id": str(thread.get("peer_architect_id", "") or ""),
            "peer_name": str(thread.get("peer_name", "") or ""),
            "last_message_at": float(thread.get("last_message_at", 0) or 0),
        }
        if latest_ack:
            item["message_id"] = str(latest_ack.get("id", "") or "")
            item["ack_required_at"] = float(latest_ack.get("timestamp", 0) or 0)
            context = latest_ack.get("context", {})
            if isinstance(context, dict):
                item["context"] = _peer_row_context({
                    "context_task_ids": context.get("task_ids", []),
                    "context_engineer_ids": context.get("engineer_ids", []),
                    "context_decision_ids": context.get("decision_ids", []),
                    "context_summary": context.get("summary", ""),
                })
        items.append({
            key: value for key, value in item.items()
            if value not in ("", None, {}, [])
        })
    return {
        "count": len(items),
        "items": items,
        "source_truncated": len(rows) >= _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
        "load_limit": _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
    }


def _architect_attention_peer_ack_items(state, caller_id: str,
                                        limit: int) -> dict:
    section = _architect_peer_ack_candidates(state, caller_id)
    items = list(section.get("items", []) or [])
    payload = {
        "count": len(items),
        "items": items[:limit],
        "truncated": (
            len(items) > limit or bool(section.get("source_truncated"))
        ),
    }
    if section.get("error"):
        payload["error"] = section.get("error")
    return payload


def _architect_attention_digest_json(state, architect_id: str,
                                     architect_group: str,
                                     args: dict) -> tuple[str, bool]:
    limit, limit_error = _normalize_architect_attention_limit(
        args.get("limit_per_section")
    )
    if limit_error:
        return limit_error, True

    hired_engineer_ids = _architect_hired_engineer_ids(state, architect_id)
    group_tasks = [
        task for task in state.board_tasks.values()
        if str(getattr(task, "group", "") or "").strip() == architect_group
        and getattr(task, "lane", "") != ARCHIVED_LANE
    ]
    open_tasks = [task for task in group_tasks if not board_task_is_closed(task)]
    actionable_tasks = [
        task for task in open_tasks
        if (
            not task_is_engineer_message_followup(task)
            and not _task_parked_or_deferred(task)
        )
    ]
    parked_deferred = [
        task for task in open_tasks
        if (
            not task_is_engineer_message_followup(task)
            and _task_parked_or_deferred(task)
        )
    ]

    blocking_asks = [
        _architect_attention_task_item(
            task,
            created_by=_task_created_by_classifier(task),
        )
        for task in actionable_tasks
        if "torque:human" in set(getattr(task, "labels", []) or [])
    ]
    blocking_asks.sort(key=lambda item: (item.get("updated_at", ""), item["id"]),
                       reverse=True)

    engineer_questions = []
    for engineer_id in sorted(hired_engineer_ids):
        engineer = state.agents.get(engineer_id)
        group = str(getattr(engineer, "group", "") or "").strip()
        if not group:
            continue
        settings = state.get_engineer_settings(group)
        actor_id = str(
            getattr(settings, "pending_question_actor_id", "") or ""
        ).strip()
        question = str(getattr(settings, "pending_question", "") or "").strip()
        if not question or actor_id != engineer_id:
            continue
        engineer_questions.append({
            "engineer_id": engineer_id,
            "engineer_name": str(getattr(engineer, "name", "") or "")
            or engineer_id,
            "group": group,
            "question": question,
            "set_at": float(
                getattr(settings, "pending_question_set_at", 0.0) or 0.0
            ),
            "paused": bool(getattr(settings, "paused", False)),
        })
    engineer_questions.sort(
        key=lambda item: (float(item.get("set_at", 0) or 0), item["engineer_id"]),
        reverse=True,
    )

    streams = compute_worktree_streams(
        state,
        group=architect_group,
        visibility_limit=limit,
        include_orphaned=False,
    )
    hired_streams = [
        stream for stream in streams
        if _stream_owned_by_hired_engineer(state, stream, hired_engineer_ids)
    ]
    ready_to_merge = [
        _architect_attention_stream_item(stream)
        for stream in hired_streams
        if str((stream or {}).get("state", "") or "") == "ready_to_merge"
    ]
    ready_to_merge.sort(
        key=lambda item: (item.get("last_activity_at", ""), item["stream_id"]),
        reverse=True,
    )

    blocker_or_stale_streams = []
    for stream in hired_streams:
        stream_state = str((stream or {}).get("state", "") or "")
        stale = _stream_stale_base(stream)
        if (
            stream_state == "fixing_blockers"
            or bool((stream or {}).get("branch_advanced", False))
            or bool(stale.get("stale"))
            or _stream_recommended_next_action(stream) == "rebase"
        ):
            blocker_or_stale_streams.append(
                _architect_attention_stream_item(stream)
            )
    blocker_or_stale_streams.sort(
        key=lambda item: (
            0 if item.get("recommended_next_action") == "rebase" else 1,
            item.get("last_activity_at", ""),
            item["stream_id"],
        )
    )

    unhealthy_tasks = [
        _architect_attention_task_item(
            task,
            created_by=_task_created_by_classifier(task),
        )
        for task in actionable_tasks
        if str(getattr(task, "health_state", "") or "healthy").strip()
        != "healthy"
    ]
    unhealthy_tasks.sort(
        key=lambda item: (
            -HEALTH_SEVERITY.get(item.get("health_state", ""), 0),
            item.get("health_since", ""),
            item.get("title", "").lower(),
        )
    )

    unhealthy_streams = []
    for stream in hired_streams:
        task_items = _stream_unhealthy_task_items(state, stream)
        if not task_items:
            continue
        item = _architect_attention_stream_item(stream)
        item["unhealthy_tasks"] = task_items[:limit]
        item["unhealthy_task_count"] = len(task_items)
        unhealthy_streams.append(item)
    unhealthy_streams.sort(
        key=lambda item: (
            -max(
                [
                    HEALTH_SEVERITY.get(task.get("health_state", ""), 0)
                    for task in item.get("unhealthy_tasks", [])
                ] or [0]
            ),
            item.get("last_activity_at", ""),
            item.get("stream_id", ""),
        )
    )

    pending_hires = [
        {
            "id": str(hire.get("id", "") or ""),
            "requested_name": str(hire.get("requested_name", "") or ""),
            "requested_provider": str(hire.get("requested_provider", "") or ""),
            "requested_specializations": list(
                hire.get("requested_specializations", []) or []
            ),
            "created_at": int(hire.get("created_at", 0) or 0),
        }
        for hire in state.load_pending_hires(
            status_filter="pending",
            architect_id=architect_id,
        )
    ]

    sections = {
        "blocking_asks": _bounded_items(blocking_asks, limit),
        "engineer_pending_questions": _bounded_items(engineer_questions, limit),
        "peer_ack_required": _architect_attention_peer_ack_items(
            state,
            architect_id,
            limit,
        ),
        "ready_to_merge_streams": _bounded_items(ready_to_merge, limit),
        "blocker_or_stale_streams": _bounded_items(
            blocker_or_stale_streams,
            limit,
        ),
        "unhealthy_tasks": _bounded_items(unhealthy_tasks, limit),
        "unhealthy_streams": _bounded_items(unhealthy_streams, limit),
        "pending_hires": _bounded_items(pending_hires, limit),
    }
    attention_total = sum(
        int(section.get("count", 0) or 0)
        for section in sections.values()
    )
    return _compact_json({
        "type": "architect_attention_digest",
        "group": architect_group,
        "limit_per_section": limit,
        "attention_count": attention_total,
        "sections": sections,
        "parked_deferred": {
            "count": len(parked_deferred),
            "note": (
                "Parked/deferred items are low-priority context only and are "
                "not included in attention_count."
            ),
        },
        "scoping": {
            "group": architect_group,
            "hired_engineer_ids": sorted(hired_engineer_ids),
            "ready_merge_and_stream_loops": "hired_engineers_only",
            "tasks": "same_group_open_non_archived",
            "pending_hires": "caller_architect_only",
            "peer_messages": "caller_threads_only",
        },
    }), False


def _architect_task_creator_filter_matches(task, creator_filter: str
                                           ) -> tuple[bool, str]:
    creator_filter = str(creator_filter or "").strip()
    if not creator_filter:
        return True, ""
    lower = creator_filter.lower()
    created_by = _task_created_by_classifier(task)
    if lower == "user":
        return created_by == "user", ""
    if lower == "architect":
        return created_by.startswith("architect:"), ""
    if lower == "system":
        return created_by == "system", ""
    if lower.startswith("engineer:"):
        engineer_id = creator_filter.split(":", 1)[1].strip()
        if not engineer_id:
            return False, "creator_filter engineer:<id> requires an id"
        created_by_engineer_id = str(
            getattr(task, "created_by_engineer_id", "") or ""
        ).strip()
        return created_by_engineer_id == engineer_id, ""
    return (
        False,
        "creator_filter must be one of: user, architect, engineer:<id>, system",
    )


def _validate_architect_task_creator_filter(creator_filter: str) -> str:
    if not str(creator_filter or "").strip():
        return ""
    _matches, error = _architect_task_creator_filter_matches(
        None,
        creator_filter,
    )
    return error


def _architect_task_list_sort_key(state, item: dict) -> tuple[int, int, str, str]:
    lane_order = {lane: idx for idx, lane in enumerate(state.board_lanes)}
    task = state.board_tasks.get(item.get("id", ""))
    return (
        lane_order.get(item.get("lane", ""), len(lane_order)),
        getattr(task, "position", 0) if task else 0,
        str(item.get("title", "") or "").lower(),
        str(item.get("id", "") or ""),
    )


def _compact_json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _architect_board_summary_json(summary: dict, task_items: list[dict]) -> str:
    """Attach a bounded task excerpt and keep architect summaries under budget."""
    count = len(task_items)
    limit = min(count, _ARCHITECT_BOARD_SUMMARY_TASK_LIMIT)
    while True:
        summary["tasks"] = {
            "count": count,
            "items": task_items[:limit],
            "truncated": count > limit,
        }
        text = _compact_json(summary)
        if len(text) <= _ARCHITECT_BOARD_SUMMARY_RESPONSE_LIMIT or limit <= 0:
            return text
        limit -= 1


def _normalize_architect_wave_summary_limit(value) -> tuple[int, str]:
    if value in (None, ""):
        return _ARCHITECT_WAVE_SUMMARY_DEFAULT_LIMIT, ""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 0, "limit_per_section must be an integer"
    if limit < 1:
        return 0, "limit_per_section must be at least 1"
    return min(limit, _ARCHITECT_WAVE_SUMMARY_MAX_LIMIT), ""


def _normalize_architect_completion_audit_limit(value) -> tuple[int, str]:
    if value in (None, ""):
        return _ARCHITECT_COMPLETION_AUDIT_DEFAULT_LIMIT, ""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 0, "limit_per_section must be an integer"
    if limit < 1:
        return 0, "limit_per_section must be at least 1"
    return min(limit, _ARCHITECT_COMPLETION_AUDIT_MAX_LIMIT), ""


def _wave_summary_text(value, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if limit > 0 and len(text) > limit:
        return text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def _wave_summary_unknown() -> str:
    return "unknown/not recorded"


def _wave_summary_known(value, *, limit: int = 240):
    text = _wave_summary_text(value, limit=limit)
    return text if text else _wave_summary_unknown()


def _wave_summary_bool_known(value):
    if value is None:
        return _wave_summary_unknown()
    return bool(value)


def _wave_summary_task_sort_key(task) -> tuple[int, int, str, str]:
    try:
        depth = int(getattr(task, "pipeline_depth", 0) or 0)
    except (TypeError, ValueError):
        depth = 0
    try:
        position = int(getattr(task, "position", 0) or 0)
    except (TypeError, ValueError):
        position = 0
    return (
        depth,
        position,
        str(getattr(task, "created_at", "") or ""),
        str(getattr(task, "id", "") or ""),
    )


def _wave_summary_category(task) -> str:
    specialization = _wave_summary_text(
        getattr(task, "suggested_specialization", "") or "",
        limit=80,
    )
    if specialization:
        return specialization
    ignored = {
        "p0", "p1", "p2", "p3", "priority", "urgent", "deferred",
        "parked", "hold", "torque:hold", "torque:human",
    }
    labels = [
        str(label or "").strip()
        for label in (getattr(task, "labels", []) or [])
        if str(label or "").strip()
    ]
    for label in labels:
        if label.lower() not in ignored:
            return label
    return "uncategorized"


def _wave_summary_task_base(task) -> dict:
    item = {
        "id": str(getattr(task, "id", "") or ""),
        "title": _summary_task_title(task),
        "lane": str(getattr(task, "lane", "") or ""),
        "status": str(getattr(task, "status", "") or ""),
        "action": str(getattr(task, "action_name", "") or ""),
        "labels": list(getattr(task, "labels", []) or []),
        "category": _wave_summary_category(task),
        "assigned_engineer_id": _effective_assigned_engineer_id(task),
        "assigned_architect_id": str(getattr(task, "assigned_architect_id", "") or "").strip(),
        "agent_id": str(getattr(task, "agent_id", "") or "").strip(),
        "updated_at": str(getattr(task, "updated_at", "") or ""),
    }
    return {
        key: value for key, value in item.items()
        if value not in ("", None, [], {})
    }


def _wave_summary_latest_message(task, actions: set[str]) -> dict:
    for entry in reversed(getattr(task, "messages", []) or []):
        if not isinstance(entry, dict):
            continue
        action = str(entry.get("action", "") or "").strip()
        if action not in actions:
            continue
        return {
            key: value for key, value in {
                "action": action,
                "message": _wave_summary_text(entry.get("message", ""), limit=500),
                "agent_name": _wave_summary_text(
                    entry.get("agent_name", ""), limit=120
                ),
                "timestamp": entry.get("timestamp", ""),
            }.items()
            if value not in ("", None)
        }
    return {}


def _wave_summary_verification(task) -> dict:
    summary = getattr(task, "verification_summary", {}) or {}
    if not isinstance(summary, dict):
        summary = {}
    evidence = {}
    for field_name, limit in (
            ("verification_state", 80),
            ("verification_mode", 80),
            ("verification_notes", 500),
            ("verification_updated_at", 120),
            ("verification_updated_by", 120),
    ):
        value = _wave_summary_text(getattr(task, field_name, "") or "", limit=limit)
        if value:
            evidence[field_name.replace("verification_", "")] = value
    for key in (
            "tests_run",
            "test_outcome",
            "isolated_rerun_evidence",
            "human_validation_pending",
            "reviewer_acceptance",
    ):
        value = _wave_summary_text(summary.get(key, ""), limit=500)
        if value:
            evidence[key] = value
    for key in (
            "manual_smoke_done",
            "deploy_needed",
            "deploy_attempted",
            "full_suite_attempted",
            "unrelated_flake_accepted",
            "live_smoke_pending",
    ):
        if key in summary:
            evidence[key] = bool(summary.get(key))
    return evidence


def _wave_summary_merge_and_boundary(task) -> dict:
    evidence = getattr(task, "completion_evidence", {}) or {}
    if not isinstance(evidence, dict):
        evidence = {}
    merge = evidence.get("merge", {}) or {}
    if not isinstance(merge, dict):
        merge = {}
    boundary = getattr(task, "worktree_boundary", {}) or {}
    if not isinstance(boundary, dict):
        boundary = {}
    pr = {}
    for candidate in (merge.get("pr"), boundary.get("pr")):
        if isinstance(candidate, dict):
            pr.update({k: v for k, v in candidate.items() if v not in ("", None)})
    pr_url = (
        merge.get("pr_url")
        or pr.get("url")
        or boundary.get("pr_url")
        or ""
    )
    squash_sha = (
        merge.get("sha")
        or merge.get("merge_commit_sha")
        or boundary.get("merge_commit_sha")
        or pr.get("merge_commit_sha")
        or ""
    )
    origin = merge.get("origin", {}) if isinstance(merge.get("origin"), dict) else {}
    origin_verified = (
        bool(merge.get("origin_verified"))
        if "origin_verified" in merge
        else origin.get("verified")
        if "verified" in origin
        else None
    )
    reviewed_boundary = {
        "task_id": str(getattr(task, "id", "") or ""),
        "commit_sha": _wave_summary_known(boundary.get("commit_sha", ""), limit=80),
        "status": _wave_summary_known(boundary.get("status", ""), limit=80),
        "recorded_at": _wave_summary_known(boundary.get("recorded_at", ""), limit=120),
    }
    return {
        "pr_url": _wave_summary_known(pr_url, limit=500),
        "squash_sha": _wave_summary_known(squash_sha, limit=80),
        "reviewed_boundary": reviewed_boundary,
        "origin_verification": {
            "verified": _wave_summary_bool_known(origin_verified),
            "summary": _wave_summary_known(
                merge.get("origin_summary")
                or origin.get("summary")
                or "",
                limit=240,
            ),
            "ref": _wave_summary_known(
                merge.get("origin_ref")
                or origin.get("ref")
                or "",
                limit=120,
            ),
            "sha": _wave_summary_known(
                merge.get("origin_sha")
                or origin.get("sha")
                or "",
                limit=80,
            ),
        },
    }


def _wave_summary_review(task) -> dict:
    evidence = getattr(task, "completion_evidence", {}) or {}
    if not isinstance(evidence, dict):
        return {}
    review = evidence.get("review", {}) or {}
    if not isinstance(review, dict) or not review:
        return {}
    out = {}
    for key in (
            "verdict",
            "follow_up_classification",
            "source_action",
            "derived_action",
            "derived_task_id",
            "recorded_at",
            "agent_name",
    ):
        value = _wave_summary_text(review.get(key, ""), limit=240)
        if value:
            out[key] = value
    summary = _wave_summary_text(review.get("summary", ""), limit=500)
    if summary:
        out["summary"] = summary
    return out


def _wave_summary_evidence(task) -> dict:
    verification = _wave_summary_verification(task)
    review = _wave_summary_review(task)
    latest_done = _wave_summary_latest_message(
        task,
        {"done", "review_verdict", "verify"},
    )
    evidence = _wave_summary_merge_and_boundary(task)
    if verification:
        evidence["verification"] = verification
    else:
        evidence["verification"] = _wave_summary_unknown()
    if review:
        evidence["review"] = review
    latest = latest_done
    if latest:
        evidence["latest_completion_message"] = latest
    return evidence


def _wave_summary_task_item(task, *, include_evidence: bool = False) -> dict:
    item = _wave_summary_task_base(task)
    if include_evidence:
        item["evidence"] = _wave_summary_evidence(task)
    return item


def _wave_summary_collect_task_ids(state, seed_ids: list[str]) -> list[str]:
    visible = set(getattr(state, "board_tasks", {}) or {})
    collected: list[str] = []
    seen: set[str] = set()

    def include(task_id: str):
        task_id = str(task_id or "").strip()
        if not task_id or task_id in seen or task_id not in visible:
            return
        seen.add(task_id)
        collected.append(task_id)

    for seed_id in seed_ids:
        seed = state.board_tasks.get(seed_id)
        if not seed:
            continue
        root_id = str(getattr(seed, "pipeline_root_id", "") or "").strip()
        if root_id and root_id in visible:
            include(root_id)
        include(seed_id)
        for task in state.board_tasks.values():
            if str(getattr(task, "id", "") or "") == seed_id:
                continue
            if (
                root_id
                and str(getattr(task, "pipeline_root_id", "") or "").strip()
                == root_id
            ):
                include(task.id)
                continue
            parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
            while parent_id:
                if parent_id == seed_id:
                    include(task.id)
                    break
                parent = state.board_tasks.get(parent_id)
                if not parent:
                    break
                parent_id = str(getattr(parent, "parent_task_id", "") or "").strip()
    collected.sort(
        key=lambda task_id: _wave_summary_task_sort_key(state.board_tasks[task_id])
    )
    return collected


def _architect_wave_scope_from_args(state, architect_id: str, architect_group: str,
                                    args: dict) -> tuple[dict, str, bool]:
    decision_id = str(args.get("decision_id", "") or "").strip()
    explicit_task_ids = _dedupe_strings(args.get("task_ids", []))
    if bool(decision_id) == bool(explicit_task_ids):
        return {}, "Provide exactly one of decision_id or task_ids", True

    visible_tasks = _filter_tasks_for_caller(state, "architect", architect_id)
    seed_task_ids: list[str] = []
    decision_payload = {}
    if decision_id:
        decision, decision_error = _load_same_group_architect_decision(
            state,
            architect_id,
            decision_id,
        )
        if not decision:
            return {}, decision_error, True
        seed_task_ids = [
            task_id for task_id in _dedupe_strings(
                decision.get("linked_task_ids", [])
            )
            if task_id in visible_tasks
        ]
        decision_payload = {
            "id": decision.get("id", ""),
            "title": decision.get("title", ""),
            "status": decision.get("status", ""),
            "linked_task_ids": list(decision.get("linked_task_ids", []) or []),
            "linked_engineer_ids": list(
                decision.get("linked_engineer_ids", []) or []
            ),
        }
    else:
        for task_ident in explicit_task_ids:
            task_id = _resolve_task(state, task_ident)
            if not task_id or task_id not in visible_tasks:
                return {}, f"Task not found: {task_ident}", True
            seed_task_ids.append(task_id)

    if seed_task_ids:
        scoped_view = copy.copy(state)
        scoped_view.board_tasks = dict(visible_tasks)
        task_ids = _wave_summary_collect_task_ids(scoped_view, seed_task_ids)
    else:
        task_ids = []
    return {
        "decision_id": decision_id,
        "decision": decision_payload,
        "task_ids": explicit_task_ids,
        "seed_task_ids": seed_task_ids,
        "expanded_task_ids": task_ids,
        "visible_tasks": visible_tasks,
        "tasks": [
            visible_tasks[task_id]
            for task_id in task_ids
            if task_id in visible_tasks
        ],
        "group": architect_group,
    }, "", False


def _completion_audit_task_item(task, *, gate_reasons: list[str] | None = None,
                                include_evidence: bool = False) -> dict:
    item = _wave_summary_task_item(task, include_evidence=include_evidence)
    if gate_reasons:
        item["gate_reasons"] = list(gate_reasons)
    health_state = str(getattr(task, "health_state", "") or "").strip()
    if health_state and health_state != "healthy":
        item["health_state"] = health_state
        item["health_since"] = str(getattr(task, "health_since", "") or "")
    return {key: value for key, value in item.items()
            if value not in ("", None, [], {})}


def _completion_audit_task_gate_reasons(task) -> list[str]:
    reasons = []
    if not task_counts_as_done(task):
        lane = str(getattr(task, "lane", "") or "").strip()
        if lane == ARCHIVED_LANE:
            reasons.append("archived_without_done_evidence")
        else:
            reasons.append("not_done")
    labels = {
        str(label or "").strip().lower()
        for label in (getattr(task, "labels", []) or [])
    }
    if "torque:human" in labels:
        reasons.append("blocking_human_ask")
    health_state = str(getattr(task, "health_state", "") or "healthy").strip()
    if health_state and health_state != "healthy":
        reasons.append("unhealthy")
    return reasons


def _completion_audit_verification_caveats(task) -> list[dict]:
    task_id = str(getattr(task, "id", "") or "")
    title = _summary_task_title(task)
    evidence = _wave_summary_verification(task)
    summary = getattr(task, "verification_summary", {}) or {}
    if not isinstance(summary, dict):
        summary = {}
    caveats = []

    state = str(getattr(task, "verification_state", "") or "").strip()
    if not evidence:
        caveats.append({
            "task_id": task_id,
            "title": title,
            "kind": "verification_unknown",
            "severity": "caveat",
            "message": "No verification evidence is recorded for this in-scope task.",
        })
    elif state and state != "passed":
        caveats.append({
            "task_id": task_id,
            "title": title,
            "kind": "verification_not_passed",
            "severity": "gate" if state in {"failed", "pending"} else "caveat",
            "state": state,
        })

    tests_run = str(summary.get("tests_run", "") or "").strip()
    if not tests_run:
        caveats.append({
            "task_id": task_id,
            "title": title,
            "kind": "tests_unknown",
            "severity": "caveat",
            "message": "Recorded tests/checks are missing.",
        })

    deploy_needed = summary.get("deploy_needed")
    deploy_attempted = summary.get("deploy_attempted")
    live_smoke_pending = summary.get("live_smoke_pending")
    human_validation_pending = str(
        summary.get("human_validation_pending", "") or ""
    ).strip()
    if human_validation_pending:
        caveats.append({
            "task_id": task_id,
            "title": title,
            "kind": "human_validation_pending",
            "severity": "gate",
            "message": human_validation_pending,
        })
    if deploy_needed is True:
        caveats.append({
            "task_id": task_id,
            "title": title,
            "kind": "deploy_needed",
            "severity": "caveat",
            "deploy_attempted": bool(deploy_attempted),
        })
    elif deploy_needed is None:
        caveats.append({
            "task_id": task_id,
            "title": title,
            "kind": "deploy_need_unknown",
            "severity": "caveat",
            "message": "Deploy requirement is not recorded.",
        })
    if live_smoke_pending is True:
        caveats.append({
            "task_id": task_id,
            "title": title,
            "kind": "live_smoke_pending",
            "severity": "caveat",
        })
    elif live_smoke_pending is None:
        caveats.append({
            "task_id": task_id,
            "title": title,
            "kind": "live_smoke_unknown",
            "severity": "caveat",
            "message": "Live-smoke state is not recorded.",
        })
    return caveats


def _completion_audit_scope_engineer_ids(tasks: list, decision: dict) -> set[str]:
    engineer_ids = {
        str(engineer_id or "").strip()
        for engineer_id in (decision.get("linked_engineer_ids", []) or [])
        if str(engineer_id or "").strip()
    }
    for task in tasks:
        for value in (
            _effective_assigned_engineer_id(task),
            str(getattr(task, "created_by_engineer_id", "") or "").strip(),
        ):
            if value:
                engineer_ids.add(value)
    return engineer_ids


def _completion_audit_engineer_questions(state, engineer_ids: set[str]) -> list[dict]:
    questions = []
    for engineer_id in sorted(engineer_ids):
        engineer = state.agents.get(engineer_id)
        group = str(getattr(engineer, "group", "") or "").strip()
        if not group:
            continue
        settings = state.get_engineer_settings(group)
        actor_id = str(
            getattr(settings, "pending_question_actor_id", "") or ""
        ).strip()
        question = str(getattr(settings, "pending_question", "") or "").strip()
        if not question or actor_id != engineer_id:
            continue
        questions.append({
            "engineer_id": engineer_id,
            "engineer_name": str(getattr(engineer, "name", "") or "")
            or engineer_id,
            "group": group,
            "question": question,
            "set_at": float(
                getattr(settings, "pending_question_set_at", 0.0) or 0.0
            ),
            "paused": bool(getattr(settings, "paused", False)),
        })
    questions.sort(
        key=lambda item: (float(item.get("set_at", 0) or 0), item["engineer_id"]),
        reverse=True,
    )
    return questions


def _completion_audit_peer_ack_items(state, architect_id: str, *,
                                     task_ids: set[str],
                                     engineer_ids: set[str],
                                     decision_id: str,
                                     limit: int) -> dict:
    section = _architect_peer_ack_candidates(state, architect_id)
    items = []
    for item in section.get("items", []) if isinstance(section, dict) else []:
        context = item.get("context", {}) if isinstance(item, dict) else {}
        if not isinstance(context, dict):
            context = {}
        context_task_ids = {
            str(task_id or "").strip()
            for task_id in (context.get("task_ids", []) or [])
            if str(task_id or "").strip()
        }
        context_engineer_ids = {
            str(engineer_id or "").strip()
            for engineer_id in (context.get("engineer_ids", []) or [])
            if str(engineer_id or "").strip()
        }
        context_decision_ids = {
            str(item_id or "").strip()
            for item_id in (context.get("decision_ids", []) or [])
            if str(item_id or "").strip()
        }
        has_context = bool(context_task_ids or context_engineer_ids
                           or context_decision_ids)
        matches_scope = bool(
            context_task_ids & task_ids
            or context_engineer_ids & engineer_ids
            or (decision_id and decision_id in context_decision_ids)
        )
        if not matches_scope and has_context:
            continue
        scoped_item = dict(item)
        scoped_item["scope_match"] = "matched" if matches_scope else "unknown_context"
        items.append(scoped_item)
    payload = {
        **_bounded_items(items, limit),
        "truncated": bool(section.get("source_truncated")) or len(items) > limit,
        "source_truncated": bool(section.get("source_truncated")),
        "load_limit": int(
            section.get("load_limit", _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT)
            or _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT
        ),
        "scan": "all_loaded_peer_ack_threads_filtered_before_output_bound",
    }
    if section.get("error"):
        payload["error"] = section.get("error")
    return payload


def _completion_audit_branch_sections(state, architect_group: str,
                                      task_ids: set[str],
                                      limit: int) -> dict:
    streams = []
    for stream in compute_worktree_streams(
            state,
            group=architect_group,
            visibility_limit=limit,
            include_orphaned=False,
    ):
        if not (member_task_ids_for_stream(stream) & task_ids):
            continue
        stream_state = str((stream or {}).get("state", "") or "").strip()
        if stream_state == "merged":
            continue
        item = _architect_attention_stream_item(stream)
        if item:
            streams.append(item)
    streams.sort(
        key=lambda item: (
            0 if item.get("state") == "ready_to_merge" else 1,
            item.get("last_activity_at", ""),
            item.get("stream_id", ""),
        )
    )
    ready = [
        item for item in streams
        if str(item.get("state", "") or "") == "ready_to_merge"
    ]
    return {
        "ready_to_merge": _bounded_items(ready, limit),
        "open_or_unmerged": _bounded_items(streams, limit),
        "note": (
            "Any in-scope stream that is not merged is treated as an open "
            "branch-boundary gate; ready_to_merge means it may be ready for "
            "the engineer merge surface, not already complete."
        ),
    }


def _wave_summary_group_completed(tasks: list, limit: int) -> dict:
    groups: dict[str, list[dict]] = {}
    for task in tasks:
        category = _wave_summary_category(task)
        groups.setdefault(category, []).append(
            _wave_summary_task_item(task, include_evidence=True)
        )
    categories = []
    for category in sorted(groups):
        items = groups[category]
        categories.append({
            "category": category,
            "count": len(items),
            "items": items[:limit],
            "truncated": len(items) > limit,
        })
    count = sum(len(items) for items in groups.values())
    return {
        "count": count,
        "categories": categories[:limit],
        "truncated": len(categories) > limit,
    }


def _wave_summary_missing_evidence_count(tasks: list) -> int:
    count = 0
    unknown = _wave_summary_unknown()
    for task in tasks:
        evidence = _wave_summary_evidence(task)
        if (
            evidence.get("pr_url") == unknown
            and evidence.get("squash_sha") == unknown
            and evidence.get("verification") == unknown
        ):
            count += 1
    return count


def _architect_wave_summary_json(state, architect_id: str,
                                 architect_group: str,
                                 args: dict) -> tuple[str, bool]:
    limit, limit_error = _normalize_architect_wave_summary_limit(
        args.get("limit_per_section")
    )
    if limit_error:
        return limit_error, True

    decision_id = str(args.get("decision_id", "") or "").strip()
    explicit_task_ids = _dedupe_strings(args.get("task_ids", []))
    if bool(decision_id) == bool(explicit_task_ids):
        return "Provide exactly one of decision_id or task_ids", True

    visible_tasks = _filter_tasks_for_caller(state, "architect", architect_id)
    seed_task_ids: list[str] = []
    decision_payload = {}
    if decision_id:
        decision, decision_error = _load_same_group_architect_decision(
            state,
            architect_id,
            decision_id,
        )
        if not decision:
            return decision_error, True
        seed_task_ids = [
            task_id for task_id in _dedupe_strings(
                decision.get("linked_task_ids", [])
            )
            if task_id in visible_tasks
        ]
        decision_payload = {
            "id": decision.get("id", ""),
            "title": decision.get("title", ""),
            "status": decision.get("status", ""),
            "linked_task_ids": list(decision.get("linked_task_ids", []) or []),
            "linked_engineer_ids": list(
                decision.get("linked_engineer_ids", []) or []
            ),
        }
    else:
        for task_ident in explicit_task_ids:
            task_id = _resolve_task(state, task_ident)
            if not task_id or task_id not in visible_tasks:
                return f"Task not found: {task_ident}", True
            seed_task_ids.append(task_id)

    if not seed_task_ids:
        return _compact_json({
            "type": "wave_summary",
            "source": {
                "decision_id": decision_id,
                "task_ids": explicit_task_ids,
                "seed_task_ids": [],
            },
            "group": architect_group,
            "summary": "No visible linked tasks were found.",
            "sections": {
                "completed_by_category": {
                    "count": 0, "categories": [], "truncated": False,
                },
                "remaining_active": _bounded_items([], limit),
                "parked_deferred": _bounded_items([], limit),
            },
            "scoping": {
                "tasks": "same_group_visible_to_calling_architect",
                "decisions": "caller_architect_only",
            },
        }), False

    scoped_view = copy.copy(state)
    scoped_view.board_tasks = dict(visible_tasks)
    task_ids = _wave_summary_collect_task_ids(scoped_view, seed_task_ids)
    tasks = [visible_tasks[task_id] for task_id in task_ids if task_id in visible_tasks]
    completed = [
        task for task in tasks
        if task_counts_as_done(task) and not _task_parked_or_deferred(task)
    ]
    remaining = [
        task for task in tasks
        if not board_task_is_closed(task) and not _task_parked_or_deferred(task)
    ]
    parked_deferred = [
        task for task in tasks
        if _task_parked_or_deferred(task)
    ]
    remaining.sort(key=_wave_summary_task_sort_key)
    parked_deferred.sort(key=_wave_summary_task_sort_key)

    caveats = [
        (
            "This is a bounded drafting aid; the architect remains responsible "
            "for final judgment."
        ),
        (
            "Deploy/restart/live-smoke evidence is reported only when recorded; "
            "worker-context deploys/restarts should remain pending unless a "
            "non-worker operator records them."
        ),
        (
            "Missing PR, squash SHA, reviewed boundary, origin, or test evidence "
            "is marked unknown/not recorded rather than inferred."
        ),
    ]
    payload = {
        "type": "wave_summary",
        "source": {
            "decision_id": decision_id,
            "decision": decision_payload,
            "task_ids": explicit_task_ids,
            "seed_task_ids": seed_task_ids,
            "expanded_task_ids": task_ids,
        },
        "group": architect_group,
        "limit_per_section": limit,
        "counts": {
            "seed_tasks": len(seed_task_ids),
            "expanded_tasks": len(tasks),
            "completed": len(completed),
            "remaining_active": len(remaining),
            "parked_deferred": len(parked_deferred),
            "completed_missing_recorded_evidence": (
                _wave_summary_missing_evidence_count(completed)
            ),
        },
        "sections": {
            "completed_by_category": _wave_summary_group_completed(
                completed,
                limit,
            ),
            "remaining_active": _bounded_items(
                [_wave_summary_task_item(task) for task in remaining],
                limit,
            ),
            "parked_deferred": {
                **_bounded_items(
                    [_wave_summary_task_item(task) for task in parked_deferred],
                    limit,
                ),
                "note": (
                    "Parked/deferred items are excluded from shipped/actionable "
                    "completion summaries."
                ),
            },
        },
        "evidence_rules": {
            "task_set": (
                "decision linked tasks or explicit task_ids, expanded to visible "
                "pipeline/root descendants in the caller architect's group"
            ),
            "pr_url": "completion_evidence.merge.pr_url, then PR boundary url",
            "squash_sha": (
                "completion_evidence.merge.sha, then worktree boundary merge SHA"
            ),
            "origin_verification": (
                "completion_evidence.merge.origin/origin_verified only"
            ),
            "verification": (
                "task verification fields and verification_summary only"
            ),
            "parked_deferred": (
                "labels deferred/parked/hold/torque:hold or matching status"
            ),
        },
        "caveats": caveats,
        "scoping": {
            "tasks": "same_group_visible_to_calling_architect",
            "decisions": "caller_architect_only",
            "hired_engineer_visibility": "unchanged_from_architect_mcp_scope",
        },
    }
    while True:
        text = _compact_json(payload)
        if len(text) <= _ARCHITECT_WAVE_SUMMARY_RESPONSE_LIMIT or limit <= 1:
            return text, False
        limit -= 1
        payload["limit_per_section"] = limit
        payload["sections"]["completed_by_category"] = _wave_summary_group_completed(
            completed,
            limit,
        )
        payload["sections"]["remaining_active"] = _bounded_items(
            [_wave_summary_task_item(task) for task in remaining],
            limit,
        )
        payload["sections"]["parked_deferred"] = {
            **_bounded_items(
                [_wave_summary_task_item(task) for task in parked_deferred],
                limit,
            ),
            "note": payload["sections"]["parked_deferred"]["note"],
        }


def _architect_completion_audit_json(state, architect_id: str,
                                     architect_group: str,
                                     args: dict) -> tuple[str, bool]:
    limit, limit_error = _normalize_architect_completion_audit_limit(
        args.get("limit_per_section")
    )
    if limit_error:
        return limit_error, True

    scope, scope_error, is_error = _architect_wave_scope_from_args(
        state,
        architect_id,
        architect_group,
        args,
    )
    if is_error:
        return scope_error, True

    tasks = list(scope.get("tasks", []) or [])
    expanded_task_ids = list(scope.get("expanded_task_ids", []) or [])
    decision_id = str(scope.get("decision_id", "") or "").strip()
    decision_payload = scope.get("decision", {}) or {}
    if not expanded_task_ids:
        payload = {
            "type": "completion_audit",
            "source": {
                "decision_id": decision_id,
                "decision": decision_payload,
                "task_ids": scope.get("task_ids", []),
                "seed_task_ids": scope.get("seed_task_ids", []),
                "expanded_task_ids": [],
            },
            "group": architect_group,
            "limit_per_section": limit,
            "recommendation": "not_complete",
            "recommendation_reason": [
                "No visible linked tasks were found for the requested scope."
            ],
            "sections": {
                "active_tasks": _bounded_items([], limit),
                "remaining_gates": _bounded_items([
                    {
                        "kind": "empty_scope",
                        "severity": "gate",
                        "message": (
                            "Completion audit requires a visible decision-linked "
                            "or explicit task scope."
                        ),
                    }
                ], limit),
                "parked_deferred": _bounded_items([], limit),
            },
            "scoping": {
                "tasks": "same_group_visible_to_calling_architect",
                "decisions": "caller_architect_only",
            },
        }
        return _compact_json(payload), False

    task_id_set = {str(task_id or "").strip() for task_id in expanded_task_ids}
    scope_engineer_ids = _completion_audit_scope_engineer_ids(
        tasks,
        decision_payload,
    )
    active_tasks = [
        task for task in tasks
        if not board_task_is_closed(task) and not _task_parked_or_deferred(task)
    ]
    active_task_items = [
        _completion_audit_task_item(
            task,
            gate_reasons=_completion_audit_task_gate_reasons(task),
        )
        for task in active_tasks
    ]
    active_task_items.sort(key=lambda item: (
        item.get("updated_at", ""),
        item.get("id", ""),
    ), reverse=True)

    accepted_scope_not_done = [
        task for task in tasks
        if not task_counts_as_done(task) and not _task_parked_or_deferred(task)
    ]
    accepted_scope_not_done.sort(key=_wave_summary_task_sort_key)
    blocking_asks = [
        _completion_audit_task_item(
            task,
            gate_reasons=["blocking_human_ask"],
        )
        for task in tasks
        if (
            not board_task_is_closed(task)
            and not _task_parked_or_deferred(task)
            and "torque:human" in {
                str(label or "").strip().lower()
                for label in (getattr(task, "labels", []) or [])
            }
        )
    ]
    blocking_asks.sort(key=lambda item: (
        item.get("updated_at", ""),
        item.get("id", ""),
    ), reverse=True)

    engineer_questions = _completion_audit_engineer_questions(
        state,
        scope_engineer_ids,
    )
    peer_ack = _completion_audit_peer_ack_items(
        state,
        architect_id,
        task_ids=task_id_set,
        engineer_ids=scope_engineer_ids,
        decision_id=decision_id,
        limit=limit,
    )
    pending_hires = [
        {
            "id": str(hire.get("id", "") or ""),
            "requested_name": str(hire.get("requested_name", "") or ""),
            "requested_provider": str(hire.get("requested_provider", "") or ""),
            "requested_specializations": list(
                hire.get("requested_specializations", []) or []
            ),
            "created_at": int(hire.get("created_at", 0) or 0),
        }
        for hire in state.load_pending_hires(
            status_filter="pending",
            architect_id=architect_id,
        )
    ]
    pending_hires.sort(key=lambda item: (
        int(item.get("created_at", 0) or 0),
        item.get("id", ""),
    ), reverse=True)

    branch_boundaries = _completion_audit_branch_sections(
        state,
        architect_group,
        task_id_set,
        limit,
    )
    parked_deferred = [
        task for task in tasks
        if _task_parked_or_deferred(task)
    ]
    parked_deferred.sort(key=_wave_summary_task_sort_key)

    verification_items = []
    for task in tasks:
        if _task_parked_or_deferred(task):
            continue
        if not task_counts_as_done(task):
            continue
        verification_items.extend(_completion_audit_verification_caveats(task))
    gate_verification_items = [
        item for item in verification_items
        if item.get("severity") == "gate"
    ]

    remaining_gates = []
    for item in active_task_items:
        remaining_gates.append({
            "kind": "active_task",
            "severity": "gate",
            "task": item,
        })
    for item in branch_boundaries["open_or_unmerged"]["items"]:
        remaining_gates.append({
            "kind": "open_branch_boundary",
            "severity": "gate",
            "stream": item,
        })
    for item in blocking_asks:
        remaining_gates.append({
            "kind": "blocking_ask",
            "severity": "gate",
            "task": item,
        })
    for item in engineer_questions:
        remaining_gates.append({
            "kind": "engineer_pending_question",
            "severity": "gate",
            "engineer_question": item,
        })
    for item in peer_ack.get("items", []):
        remaining_gates.append({
            "kind": "peer_ack_required",
            "severity": "gate",
            "peer_ack": item,
        })
    if peer_ack.get("source_truncated") and not peer_ack.get("items"):
        remaining_gates.append({
            "kind": "peer_ack_scan_truncated",
            "severity": "gate",
            "peer_ack": {
                "message": (
                    "Peer-ack source hit the internal load limit before the "
                    "audit could prove no in-scope ack obligations remain."
                ),
                "load_limit": peer_ack.get("load_limit"),
            },
        })
    for item in pending_hires:
        remaining_gates.append({
            "kind": "pending_hire",
            "severity": "gate",
            "pending_hire": item,
        })
    for task in accepted_scope_not_done:
        remaining_gates.append({
            "kind": "accepted_scope_not_done",
            "severity": "gate",
            "task": _completion_audit_task_item(
                task,
                gate_reasons=_completion_audit_task_gate_reasons(task),
            ),
        })
    for item in gate_verification_items:
        remaining_gates.append({
            "kind": item.get("kind", "verification_gate"),
            "severity": "gate",
            "verification": item,
        })

    caveat_count = len([
        item for item in verification_items
        if item.get("severity") != "gate"
    ]) + len(parked_deferred)
    gate_count = len(remaining_gates)
    if gate_count:
        recommendation = "not_complete"
    elif caveat_count:
        recommendation = "complete_with_caveats"
    else:
        recommendation = "complete"

    recommendation_reason = []
    if gate_count:
        recommendation_reason.append(
            f"{gate_count} active gate(s) remain in the requested scope."
        )
    if caveat_count:
        recommendation_reason.append(
            f"{caveat_count} caveat(s) require architect judgment or recorded evidence."
        )
    if not recommendation_reason:
        recommendation_reason.append(
            "No active gates, open branch boundaries, pending obligations, or "
            "recorded evidence caveats were found in the requested scope."
        )

    payload = {
        "type": "completion_audit",
        "source": {
            "decision_id": decision_id,
            "decision": decision_payload,
            "task_ids": scope.get("task_ids", []),
            "seed_task_ids": scope.get("seed_task_ids", []),
            "expanded_task_ids": expanded_task_ids,
        },
        "group": architect_group,
        "limit_per_section": limit,
        "recommendation": recommendation,
        "recommendation_reason": recommendation_reason,
        "counts": {
            "seed_tasks": len(scope.get("seed_task_ids", []) or []),
            "expanded_tasks": len(tasks),
            "active_tasks": len(active_tasks),
            "accepted_scope_not_done": len(accepted_scope_not_done),
            "blocking_asks": len(blocking_asks),
            "engineer_pending_questions": len(engineer_questions),
            "peer_ack_required": int(peer_ack.get("count", 0) or 0),
            "pending_hires": len(pending_hires),
            "open_branch_boundaries": int(
                branch_boundaries["open_or_unmerged"].get("count", 0) or 0
            ),
            "verification_caveats": len(verification_items),
            "parked_deferred": len(parked_deferred),
            "remaining_gates": gate_count,
        },
        "sections": {
            "active_tasks": _bounded_items(active_task_items, limit),
            "remaining_gates": _bounded_items(remaining_gates, limit),
            "branch_boundaries": branch_boundaries,
            "blocking_asks": _bounded_items(blocking_asks, limit),
            "engineer_pending_questions": _bounded_items(
                engineer_questions,
                limit,
            ),
            "peer_ack_required": peer_ack,
            "pending_hires": {
                **_bounded_items(pending_hires, limit),
                "note": (
                    "Pending hires are caller-architect scoped; hire records "
                    "are not task-scoped, so unresolved caller hires are "
                    "reported conservatively."
                ),
            },
            "accepted_scope_not_done": _bounded_items(
                [
                    _completion_audit_task_item(
                        task,
                        gate_reasons=_completion_audit_task_gate_reasons(task),
                    )
                    for task in accepted_scope_not_done
                ],
                limit,
            ),
            "parked_deferred": {
                **_bounded_items(
                    [
                        _completion_audit_task_item(task)
                        for task in parked_deferred
                    ],
                    limit,
                ),
                "note": (
                    "Parked/deferred items are separated from active blockers "
                    "and do not by themselves force not_complete."
                ),
            },
            "verification_caveats": _bounded_items(
                verification_items,
                limit,
            ),
        },
        "recommendation_rules": {
            "not_complete": (
                "Any active in-scope task, accepted-scope task not Done, "
                "open/ready branch boundary, blocking ask, pending engineer "
                "question, peer ack obligation, pending hire, or hard "
                "verification gate remains."
            ),
            "complete_with_caveats": (
                "No active gates remain, but parked/deferred exclusions or "
                "unknown/not-recorded verification, deploy, tests, or "
                "live-smoke evidence remains."
            ),
            "complete": (
                "No active gates or caveats were found in the bounded scope."
            ),
        },
        "evidence_rules": {
            "task_set": (
                "decision linked tasks or explicit task_ids, expanded to visible "
                "pipeline/root descendants in the caller architect's group"
            ),
            "done": "task lane Done or Archived-from-Done",
            "parked_deferred": (
                "labels deferred/parked/hold/torque:hold or matching status"
            ),
            "verification": (
                "task verification fields and verification_summary only; "
                "missing values are reported unknown/not recorded"
            ),
            "branch_boundaries": (
                "computed worktree streams intersecting the in-scope task ids"
            ),
            "peer_ack_required": (
                "caller architect peer threads whose context matches scope, "
                "plus ack-required threads with no recorded context marked "
                "unknown_context"
            ),
        },
        "caveats": [
            (
                "This helper is an audit aid only; it never marks goals or "
                "tasks complete and does not replace architect judgment."
            ),
            (
                "Deploy/restart/live-smoke evidence is reported only when "
                "recorded; worker-context deploys/restarts should remain "
                "pending unless a non-worker operator records them."
            ),
            (
                "Parked/deferred tasks are shown separately from active "
                "incomplete work."
            ),
        ],
        "scoping": {
            "tasks": "same_group_visible_to_calling_architect",
            "decisions": "caller_architect_only",
            "engineers": "decision-linked and in-scope assigned/creator engineers",
            "pending_hires": "caller_architect_only",
            "peer_messages": "caller_threads_matching_scope_or_unknown_context",
        },
    }
    while True:
        text = _compact_json(payload)
        if len(text) <= _ARCHITECT_COMPLETION_AUDIT_RESPONSE_LIMIT or limit <= 1:
            return text, False
        limit -= 1
        payload["limit_per_section"] = limit
        payload["sections"]["active_tasks"] = _bounded_items(
            active_task_items,
            limit,
        )
        payload["sections"]["remaining_gates"] = _bounded_items(
            remaining_gates,
            limit,
        )
        payload["sections"]["branch_boundaries"] = _completion_audit_branch_sections(
            state,
            architect_group,
            task_id_set,
            limit,
        )
        payload["sections"]["blocking_asks"] = _bounded_items(
            blocking_asks,
            limit,
        )
        payload["sections"]["engineer_pending_questions"] = _bounded_items(
            engineer_questions,
            limit,
        )
        payload["sections"]["peer_ack_required"] = _completion_audit_peer_ack_items(
            state,
            architect_id,
            task_ids=task_id_set,
            engineer_ids=scope_engineer_ids,
            decision_id=decision_id,
            limit=limit,
        )
        payload["sections"]["pending_hires"] = {
            **_bounded_items(pending_hires, limit),
            "note": payload["sections"]["pending_hires"]["note"],
        }
        payload["sections"]["accepted_scope_not_done"] = _bounded_items(
            [
                _completion_audit_task_item(
                    task,
                    gate_reasons=_completion_audit_task_gate_reasons(task),
                )
                for task in accepted_scope_not_done
            ],
            limit,
        )
        payload["sections"]["parked_deferred"] = {
            **_bounded_items(
                [_completion_audit_task_item(task) for task in parked_deferred],
                limit,
            ),
            "note": payload["sections"]["parked_deferred"]["note"],
        }
        payload["sections"]["verification_caveats"] = _bounded_items(
            verification_items,
            limit,
        )
