"""Architect event feeds, task-chain inspection, and pending questions."""

from torque.digest_routing import resolve_digest_recipients
from torque.mcp_engineer_tools.shared import resolve_task as _resolve_task
from torque.mcp_scoped.architect_reports import (
    _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
    _compact_json,
)
from torque.mcp_scoped.common import (
    _agent_peer_message_row_to_entry,
    _architect_hired_engineer_ids,
    _effective_assigned_engineer_id,
    _effective_owner_engineer_id,
    _event_task_chain,
    _is_engineer_like_cell,
    _peer_row_context,
    _resolve_architect_hired_engineer,
)
from torque.state import task_counts_as_done

_ARCHITECT_EVENTS_RECENT_DEFAULT_LIMIT = 20
_ARCHITECT_EVENTS_RECENT_MAX_LIMIT = 100
_ARCHITECT_EVENTS_RECENT_LOAD_LIMIT = 500
_ARCHITECT_EVENTS_RECENT_MESSAGE_LIMIT = 120
_ARCHITECT_EVENTS_RECENT_RESPONSE_LIMIT = 10_000
_ARCHITECT_TASK_CHAIN_NODE_LIMIT = 50


def _open_worker_ask_for_engineer(state, engineer_id: str):
    """Return one unresolved Worker ask owned by ``engineer_id``.

    Engineer-level user asks live in the group pending-question slot.  Worker
    asks instead live as board descendants, so consult their stamped assignee
    (and the reply worker for rows created before the stamp existed).  This is
    read-only discoverability for the Architect, not Architect authority to
    answer the Worker ask.
    """
    engineer_id = str(engineer_id or "").strip()
    if not engineer_id:
        return None
    for task in sorted(
            getattr(state, "board_tasks", {}).values(),
            key=lambda item: (str(getattr(item, "created_at", "") or ""),
                              str(getattr(item, "id", "") or "")),
    ):
        if task_counts_as_done(task) or "torque:human" not in (
                getattr(task, "labels", []) or []):
            continue
        if str(getattr(task, "assigned_engineer_id", "") or "").strip() == engineer_id:
            return task
        worker = getattr(state, "agents", {}).get(
            str(getattr(task, "reply_agent_id", "") or "").strip()
        )
        if str(getattr(worker, "owner_engineer_id", "") or "").strip() == engineer_id:
            return task
    return None

def _event_agent_kind(cell) -> str:
    kind = str(getattr(cell, "kind", "") or "").strip() if cell else ""
    if kind:
        return kind
    if cell and str(getattr(cell, "cell_type", "") or "").strip() == "terminal":
        return "terminal"
    return ""


def _event_attribution(state, event: dict) -> dict:
    cell_id = str((event or {}).get("cell_id", "") or "").strip()
    cell = getattr(state, "agents", {}).get(cell_id)
    task_chain = _event_task_chain(state, event)
    task = task_chain[0] if task_chain else None

    assigned_engineer_id = ""
    created_by_architect_id = ""
    created_by_engineer_id = ""
    task_agent_ids: list[str] = []
    for chain_task in task_chain:
        if not assigned_engineer_id:
            assigned_engineer_id = _effective_assigned_engineer_id(chain_task)
        if not created_by_architect_id:
            created_by_architect_id = str(
                getattr(chain_task, "created_by_architect_id", "") or ""
            ).strip()
        if not created_by_engineer_id:
            created_by_engineer_id = str(
                getattr(chain_task, "created_by_engineer_id", "") or ""
            ).strip()
        for field_name in ("agent_id", "reply_agent_id"):
            task_agent_id = str(getattr(chain_task, field_name, "") or "").strip()
            if task_agent_id:
                task_agent_ids.append(task_agent_id)

    owner_engineer_id = ""
    if cell:
        owner_engineer_id = _effective_owner_engineer_id(cell)
    if not owner_engineer_id:
        for task_agent_id in task_agent_ids:
            task_agent = getattr(state, "agents", {}).get(task_agent_id)
            owner_engineer_id = _effective_owner_engineer_id(task_agent)
            if owner_engineer_id:
                break

    agent_name = str((event or {}).get("agent_name", "") or "")
    if not agent_name and cell:
        agent_name = str(getattr(cell, "name", "") or "")

    return {
        "cell": cell,
        "task": task,
        "task_chain": task_chain,
        "task_agent_ids": task_agent_ids,
        "cell_id": cell_id,
        "agent_name": agent_name,
        "agent_kind": _event_agent_kind(cell),
        "assigned_engineer_id": assigned_engineer_id,
        "created_by_architect_id": created_by_architect_id,
        "created_by_engineer_id": created_by_engineer_id,
        "owner_engineer_id": owner_engineer_id,
    }


def _event_involves_engineer(state, event: dict, engineer_id: str,
                             attribution: dict | None = None) -> bool:
    engineer_id = str(engineer_id or "").strip()
    if not engineer_id:
        return False
    attribution = attribution or _event_attribution(state, event)
    if engineer_id in {
        str(attribution.get("assigned_engineer_id", "") or "").strip(),
        str(attribution.get("owner_engineer_id", "") or "").strip(),
        str(attribution.get("created_by_engineer_id", "") or "").strip(),
        str(attribution.get("cell_id", "") or "").strip(),
    }:
        return True
    if engineer_id in {
        str(agent_id or "").strip()
        for agent_id in attribution.get("task_agent_ids", []) or []
    }:
        return True
    for agent_id in attribution.get("task_agent_ids", []) or []:
        task_agent = getattr(state, "agents", {}).get(str(agent_id or "").strip())
        if _effective_owner_engineer_id(task_agent) == engineer_id:
            return True
    return False


def _architect_event_visible(state, caller_id: str, caller_group: str,
                             event: dict, attribution: dict) -> bool:
    if str((event or {}).get("group", "") or "").strip() != caller_group:
        return False
    caller_id = str(caller_id or "").strip()
    if str(attribution.get("cell_id", "") or "").strip() == caller_id:
        return True
    if str(attribution.get("created_by_architect_id", "") or "").strip() == caller_id:
        return True
    hired_engineers = _architect_hired_engineer_ids(state, caller_id)
    if str(attribution.get("assigned_engineer_id", "") or "").strip() in hired_engineers:
        return True
    if str(attribution.get("owner_engineer_id", "") or "").strip() in hired_engineers:
        return True
    cell = attribution.get("cell")
    if (
        _is_engineer_like_cell(state, cell)
        and str(getattr(cell, "id", "") or "").strip() in hired_engineers
    ):
        return True
    return any(
        _event_involves_engineer(state, event, engineer_id, attribution)
        for engineer_id in hired_engineers
    )


def _task_chain_sort_key(task) -> tuple[int, str, str]:
    try:
        depth = int(getattr(task, "pipeline_depth", 0) or 0)
    except (TypeError, ValueError):
        depth = 0
    return (
        depth,
        str(getattr(task, "created_at", "") or ""),
        str(getattr(task, "id", "") or ""),
    )


def _task_chain_depth(task) -> int:
    try:
        return max(0, int(getattr(task, "pipeline_depth", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _resolve_task_chain_root(state, task):
    if not state or not task:
        return None
    root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
    if root_id:
        root = state.board_tasks.get(root_id)
        if root:
            return root
    current = task
    seen = {str(getattr(task, "id", "") or "").strip()}
    while current:
        parent_id = str(getattr(current, "parent_task_id", "") or "").strip()
        if not parent_id or parent_id in seen:
            break
        parent = state.board_tasks.get(parent_id)
        if not parent:
            break
        current = parent
        seen.add(parent_id)
    return current


def _architect_task_chain_root_visible(state, caller_id: str, root_task) -> bool:
    if not state or not root_task:
        return False
    caller_id = str(caller_id or "").strip()
    if not caller_id:
        return False
    if str(getattr(root_task, "created_by_architect_id", "") or "").strip() == caller_id:
        return True
    return _effective_assigned_engineer_id(root_task) in _architect_hired_engineer_ids(
        state,
        caller_id,
    )


def _collect_task_chain_tasks(state, root_task) -> dict[str, object]:
    if not state or not root_task:
        return {}
    root_id = str(getattr(root_task, "id", "") or "").strip()
    root_group = str(getattr(root_task, "group", "") or "").strip()
    tasks_by_id: dict[str, object] = {}

    def _include(candidate) -> None:
        if not candidate:
            return
        task_id = str(getattr(candidate, "id", "") or "").strip()
        if not task_id:
            return
        if root_group and str(getattr(candidate, "group", "") or "").strip() != root_group:
            return
        tasks_by_id[task_id] = candidate

    _include(root_task)
    for task in state.board_get_chain(root_id):
        _include(task)

    pending = [root_id]
    seen: set[str] = set()
    while pending:
        current_id = pending.pop(0)
        if current_id in seen:
            continue
        seen.add(current_id)
        for child in sorted(state.board_get_children(current_id), key=_task_chain_sort_key):
            child_id = str(getattr(child, "id", "") or "").strip()
            if not child_id:
                continue
            _include(child)
            if child_id not in seen:
                pending.append(child_id)
    return tasks_by_id


def _build_task_chain_tree(root_task, tasks_by_id: dict[str, object]
                           ) -> tuple[dict, dict, bool]:
    root_id = str(getattr(root_task, "id", "") or "").strip()
    children_by_parent: dict[str, list[str]] = {
        task_id: [] for task_id in tasks_by_id
    }
    orphan_ids: list[str] = []
    for task_id, task in tasks_by_id.items():
        if task_id == root_id:
            continue
        parent_id = str(getattr(task, "parent_task_id", "") or "").strip()
        if parent_id and parent_id in tasks_by_id and parent_id != task_id:
            children_by_parent.setdefault(parent_id, []).append(task_id)
        else:
            orphan_ids.append(task_id)
    for child_ids in children_by_parent.values():
        child_ids.sort(key=lambda tid: _task_chain_sort_key(tasks_by_id[tid]))
    if orphan_ids:
        root_children = children_by_parent.setdefault(root_id, [])
        root_children.extend(sorted(
            orphan_ids,
            key=lambda tid: _task_chain_sort_key(tasks_by_id[tid]),
        ))
        root_children[:] = list(dict.fromkeys(root_children))

    stats = {
        "total_nodes": 0,
        "done": 0,
        "in_progress": 0,
        "max_depth": 0,
    }
    truncated = False
    visited: set[str] = set()

    def _build(task_id: str, depth: int) -> dict | None:
        nonlocal truncated
        if truncated or task_id in visited:
            return None
        task = tasks_by_id.get(task_id)
        if not task:
            return None
        if stats["total_nodes"] >= _ARCHITECT_TASK_CHAIN_NODE_LIMIT:
            truncated = True
            return None
        visited.add(task_id)
        stats["total_nodes"] += 1
        if task_counts_as_done(task):
            stats["done"] += 1
        if str(getattr(task, "lane", "") or "").strip() == "In Progress":
            stats["in_progress"] += 1
        stats["max_depth"] = max(stats["max_depth"], depth, _task_chain_depth(task))
        children = []
        for child_id in children_by_parent.get(task_id, []):
            child = _build(child_id, depth + 1)
            if child is not None:
                children.append(child)
            if truncated:
                break
        return {
            "task_id": task.id,
            "title": getattr(task, "task", "") or "",
            "lane": getattr(task, "lane", "") or "",
            "status": getattr(task, "status", "") or "",
            "action_name": getattr(task, "action_name", "") or "",
            "agent_id": getattr(task, "agent_id", "") or "",
            "children": children,
        }

    tree = _build(root_id, 0) or {
        "task_id": root_id,
        "title": getattr(root_task, "task", "") or "",
        "lane": getattr(root_task, "lane", "") or "",
        "status": getattr(root_task, "status", "") or "",
        "action_name": getattr(root_task, "action_name", "") or "",
        "agent_id": getattr(root_task, "agent_id", "") or "",
        "children": [],
    }
    return tree, stats, truncated


def _architect_task_chain_json(state, caller_id: str, task_ident: str) -> tuple[str, bool]:
    task_id = _resolve_task(state, task_ident)
    if not task_id:
        return "Task not found", True
    task = state.board_tasks.get(task_id)
    if not task:
        return "Task not found", True
    root_task = _resolve_task_chain_root(state, task) or task
    if not _architect_task_chain_root_visible(state, caller_id, root_task):
        return "Task chain root not visible to this architect", True
    tasks_by_id = _collect_task_chain_tasks(state, root_task)
    tree, stats, truncated = _build_task_chain_tree(root_task, tasks_by_id)
    payload = {
        "root": {
            "task_id": root_task.id,
            "title": getattr(root_task, "task", "") or "",
            "lane": getattr(root_task, "lane", "") or "",
            "status": getattr(root_task, "status", "") or "",
            "assigned_engineer_id": _effective_assigned_engineer_id(root_task),
        },
        "focus_task_id": task_id,
        "tree": tree,
        "stats": stats,
    }
    if truncated:
        payload["truncated"] = True
    return _compact_json(payload), False


def _clip_event_message(value: str) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= _ARCHITECT_EVENTS_RECENT_MESSAGE_LIMIT:
        return text
    return text[:_ARCHITECT_EVENTS_RECENT_MESSAGE_LIMIT - 1].rstrip() + "…"


def _normalize_architect_events_limit(value) -> tuple[int, str]:
    if value in (None, ""):
        return _ARCHITECT_EVENTS_RECENT_DEFAULT_LIMIT, ""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 0, "limit must be an integer"
    if limit < 1:
        return 0, "limit must be at least 1"
    return min(limit, _ARCHITECT_EVENTS_RECENT_MAX_LIMIT), ""


def _normalize_since(value) -> tuple[float, str]:
    if value in (None, ""):
        return 0.0, ""
    try:
        since = float(value)
    except (TypeError, ValueError):
        return 0.0, "since must be a number"
    return since, ""


def _load_recent_panel_events(state) -> list[dict]:
    db = getattr(state, "db", None)
    if db and hasattr(db, "load_panel_events"):
        return list(reversed(db.load_panel_events(
            limit=_ARCHITECT_EVENTS_RECENT_LOAD_LIMIT
        )))
    panel_log = getattr(state, "panel_log", None)
    if panel_log and hasattr(panel_log, "get_recent"):
        return list(reversed(panel_log.get_recent(
            _ARCHITECT_EVENTS_RECENT_LOAD_LIMIT
        )))
    return []


def _peer_row_created_at(row: dict) -> float:
    try:
        return float((row or {}).get("created_at", (row or {}).get("timestamp", 0)) or 0)
    except (TypeError, ValueError):
        return 0.0


def _load_recent_architect_peer_rows(
        state,
        architect_id: str,
        *,
        limit: int = _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
        since: float = 0.0) -> list[dict]:
    """Load recent canonical peer-message rows involving one Architect."""
    architect_id = str(architect_id or "").strip()
    if not architect_id:
        return []
    limit = max(1, min(int(limit or _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT), 1000))
    db = getattr(state, "db", None)
    if db and hasattr(db, "load_agent_peer_messages_for_agent"):
        return db.load_agent_peer_messages_for_agent(
            architect_id,
            limit=limit,
            since=since,
        )
    cell = getattr(state, "agents", {}).get(architect_id)
    rows = []
    for entry in list(getattr(cell, "mcp_messages", []) or []):
        if "user" in {
            str((entry or {}).get("sender_kind", "") or "").strip(),
            str((entry or {}).get("recipient_kind", "") or "").strip(),
        }:
            continue
        if str((entry or {}).get("action", "") or "").strip() not in {
            "architect_peer_message",
            "architect_peer_reply",
        }:
            continue
        timestamp = _peer_row_created_at(entry)
        if since and timestamp < since:
            continue
        row = dict(entry)
        row.setdefault("created_at", timestamp)
        row.setdefault("group_name", row.get("group", ""))
        rows.append(row)
    rows.sort(
        key=lambda row: (
            _peer_row_created_at(row),
            str((row or {}).get("id", "") or ""),
        ),
        reverse=True,
    )
    return rows[:limit]


def _peer_thread_pending_reply_info(messages: list[dict],
                                    caller_id: str) -> tuple[bool, float]:
    """Return whether a thread needs caller reply plus the pending ack time."""
    latest_outgoing = 0.0
    latest_incoming_ack = 0.0
    caller_id = str(caller_id or "").strip()
    for row in messages:
        ts = _peer_row_created_at(row)
        if str((row or {}).get("sender_id", "") or "").strip() == caller_id:
            latest_outgoing = max(latest_outgoing, ts)
        elif bool((row or {}).get("ack_required", False)):
            latest_incoming_ack = max(latest_incoming_ack, ts)
    return latest_incoming_ack > latest_outgoing, latest_incoming_ack


def _architect_peer_message_summary(state, architect_id: str) -> dict:
    rows = _load_recent_architect_peer_rows(
        state,
        architect_id,
        limit=_ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
    )
    architect_id = str(architect_id or "").strip()
    grouped: dict[str, list[dict]] = {}
    sent_count = 0
    received_count = 0
    unread_count = 0
    latest_message_at = 0.0
    for row in rows:
        sender_id = str((row or {}).get("sender_id", "") or "").strip()
        recipient_id = str((row or {}).get("recipient_id", "") or "").strip()
        timestamp = _peer_row_created_at(row)
        latest_message_at = max(latest_message_at, timestamp)
        if sender_id == architect_id:
            sent_count += 1
        if recipient_id == architect_id:
            received_count += 1
            delivery_state = str(
                (row or {}).get("delivery_state", "") or "buffered"
            ).strip()
            if delivery_state != "delivered":
                unread_count += 1
        thread_id = str((row or {}).get("thread_id", "") or "").strip()
        if thread_id:
            grouped.setdefault(thread_id, []).append(row)

    requires_reply_count = 0
    pending_times = []
    for messages in grouped.values():
        messages.sort(
            key=lambda row: (
                _peer_row_created_at(row),
                str((row or {}).get("id", "") or ""),
            )
        )
        requires_reply, pending_at = _peer_thread_pending_reply_info(
            messages,
            architect_id,
        )
        if requires_reply:
            requires_reply_count += 1
            if pending_at:
                pending_times.append(pending_at)

    return {
        "recent_count": len(rows),
        "sent_count": sent_count,
        "received_count": received_count,
        "unread_count": unread_count,
        "ack_required_pending_count": requires_reply_count,
        "requires_reply_count": requires_reply_count,
        "oldest_unanswered_at": min(pending_times) if pending_times else 0.0,
        "latest_message_at": latest_message_at,
        "truncated": len(rows) >= _ARCHITECT_PEER_SUMMARY_LOAD_LIMIT,
    }


def _peer_row_involves_engineer(state, row: dict, engineer_id: str) -> bool:
    engineer_id = str(engineer_id or "").strip()
    if not engineer_id:
        return False
    if engineer_id in {
        str(item or "").strip()
        for item in ((row or {}).get("context_engineer_ids", []) or [])
    }:
        return True
    for task_id in (row or {}).get("context_task_ids", []) or []:
        task = getattr(state, "board_tasks", {}).get(str(task_id or "").strip())
        if task and _effective_assigned_engineer_id(task) == engineer_id:
            return True
    return False


def _architect_peer_message_event(state, row: dict, architect_id: str,
                                  requires_reply: bool) -> dict:
    entry = _agent_peer_message_row_to_entry(row, architect_id)
    sender = getattr(state, "agents", {}).get(entry["sender_id"])
    peer = getattr(state, "agents", {}).get(entry["peer_id"])
    context = _peer_row_context(row)
    task_id = context["task_ids"][0] if context["task_ids"] else ""
    assigned_engineer_id = ""
    if task_id:
        task = getattr(state, "board_tasks", {}).get(task_id)
        assigned_engineer_id = _effective_assigned_engineer_id(task) if task else ""
    if not assigned_engineer_id and context["engineer_ids"]:
        assigned_engineer_id = context["engineer_ids"][0]
    return {
        "id": entry["id"],
        "kind": entry["action"],
        "timestamp": float(entry.get("timestamp", 0) or 0),
        "cell_id": entry["sender_id"],
        "agent_name": str(getattr(sender, "name", "") or "").strip()
        or entry["sender_id"],
        "agent_kind": entry["sender_kind"],
        "group": str((row or {}).get("group_name", (row or {}).get("group", "")) or ""),
        "task_id": task_id,
        "assigned_engineer_id": assigned_engineer_id,
        "created_by_architect_id": entry["sender_id"],
        "owner_engineer_id": "",
        "message": _clip_event_message(entry.get("message", "") or ""),
        "digest_recipients": [],
        "message_id": entry["id"],
        "thread_id": entry["thread_id"],
        "reply_to_id": entry["reply_to_id"],
        "direction": entry["direction"],
        "peer_architect_id": entry["peer_id"],
        "peer_name": str(getattr(peer, "name", "") or "").strip()
        or entry["peer_id"],
        "ack_required": bool(entry.get("ack_required", False)),
        "requires_reply": bool(requires_reply),
        "delivery_state": entry["delivery_state"],
        "context": context,
    }


def _recent_architect_peer_message_events(
        state,
        architect_id: str,
        architect_group: str,
        *,
        kind_filter: str = "",
        engineer_filter: str = "",
        since: float = 0.0) -> list[dict]:
    rows = _load_recent_architect_peer_rows(
        state,
        architect_id,
        limit=_ARCHITECT_EVENTS_RECENT_LOAD_LIMIT,
        since=since,
    )
    rows = [
        row for row in rows
        if str((row or {}).get("group_name", (row or {}).get("group", "")) or "").strip()
        == str(architect_group or "").strip()
    ]
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(
            str((row or {}).get("thread_id", "") or "").strip(),
            [],
        ).append(row)
    requires_by_thread: dict[str, bool] = {}
    for thread_id, messages in grouped.items():
        messages.sort(
            key=lambda row: (
                _peer_row_created_at(row),
                str((row or {}).get("id", "") or ""),
            )
        )
        requires_by_thread[thread_id] = _peer_thread_pending_reply_info(
            messages,
            architect_id,
        )[0]

    events = []
    for row in rows:
        action = (
            "architect_peer_reply"
            if str((row or {}).get("reply_to_id", "") or "").strip()
            else "architect_peer_message"
        )
        if kind_filter and kind_filter != action:
            continue
        if engineer_filter and not _peer_row_involves_engineer(
            state,
            row,
            engineer_filter,
        ):
            continue
        thread_id = str((row or {}).get("thread_id", "") or "").strip()
        events.append(_architect_peer_message_event(
            state,
            row,
            architect_id,
            requires_by_thread.get(thread_id, False),
        ))
    return events


def _architect_events_recent_json(state, architect_id: str, architect_group: str,
                                  args: dict) -> tuple[str, bool]:
    limit, limit_error = _normalize_architect_events_limit(args.get("limit"))
    if limit_error:
        return limit_error, True
    since, since_error = _normalize_since(args.get("since"))
    if since_error:
        return since_error, True
    kind_filter = str(args.get("kind_filter", "") or "").strip()
    engineer_filter = str(args.get("engineer_id", "") or "").strip()

    events = []
    for event in _load_recent_panel_events(state):
        if kind_filter and str(event.get("kind", "") or "").strip() != kind_filter:
            continue
        timestamp = float(event.get("timestamp", 0) or 0)
        if since and timestamp < since:
            continue
        attribution = _event_attribution(state, event)
        if not _architect_event_visible(
            state,
            architect_id,
            architect_group,
            event,
            attribution,
        ):
            continue
        if engineer_filter and not _event_involves_engineer(
            state, event, engineer_filter, attribution
        ):
            continue
        events.append({
            "id": str(event.get("id", "") or ""),
            "kind": str(event.get("kind", "") or ""),
            "timestamp": timestamp,
            "cell_id": attribution["cell_id"],
            "agent_name": attribution["agent_name"],
            "agent_kind": attribution["agent_kind"],
            "group": str(event.get("group", "") or ""),
            "task_id": str(event.get("task_id", "") or ""),
            "assigned_engineer_id": attribution["assigned_engineer_id"],
            "created_by_architect_id": attribution["created_by_architect_id"],
            "owner_engineer_id": attribution["owner_engineer_id"],
            "message": _clip_event_message(event.get("message", "") or ""),
            "digest_recipients": resolve_digest_recipients(state, event),
        })

    events.extend(_recent_architect_peer_message_events(
        state,
        architect_id,
        architect_group,
        kind_filter=kind_filter,
        engineer_filter=engineer_filter,
        since=since,
    ))
    events.sort(
        key=lambda event: (
            float((event or {}).get("timestamp", 0) or 0),
            str((event or {}).get("id", "") or ""),
        ),
        reverse=True,
    )
    truncated = len(events) > limit
    payload_events = events[:limit]
    while True:
        payload = {"events": payload_events, "truncated": truncated}
        text = _compact_json(payload)
        if (
            len(text) <= _ARCHITECT_EVENTS_RECENT_RESPONSE_LIMIT
            or not payload_events
        ):
            return text, False
        payload_events = payload_events[:-1]
        truncated = True


def _architect_engineer_pending_question_json(
        state, architect_id: str, args: dict) -> tuple[str, bool]:
    engineer_ident = str(args.get("engineer_id", "") or "").strip()
    if not engineer_ident:
        return "engineer_id is required", True
    engineer_id, engineer_error = _resolve_architect_hired_engineer(
        state, architect_id, engineer_ident
    )
    if not engineer_id:
        return engineer_error, True
    engineer = state.agents.get(engineer_id)
    engineer_group = str(getattr(engineer, "group", "") or "").strip()
    payload = {
        "type": "engineer_pending_question",
        "engineer_id": engineer_id,
        "question": "",
        "set_at": 0.0,
        "paused": False,
        "note": "",
    }
    if not engineer_group:
        payload["note"] = "Engineer has no group."
        return _compact_json(payload), False
    ws = state.get_engineer_settings(engineer_group)
    question = str(getattr(ws, "pending_question", "") or "")
    pending_owner_id = str(
        getattr(ws, "pending_question_actor_id", "") or ""
    ).strip()
    if not question or pending_owner_id != engineer_id:
        worker_ask = _open_worker_ask_for_engineer(state, engineer_id)
        if worker_ask:
            payload.update({
                "question": str(getattr(worker_ask, "task", "") or ""),
                "ask_task_id": str(getattr(worker_ask, "id", "") or ""),
                "ask_level": "worker",
                "note": (
                    "Worker-level ask awaiting the owning Engineer; resolve "
                    "it with agent_ask_answer(task=...)."
                ),
            })
            return _compact_json(payload), False
        payload["note"] = "No pending question for engineer."
        return _compact_json(payload), False
    payload.update({
        "question": question,
        "set_at": float(getattr(ws, "pending_question_set_at", 0.0) or 0.0),
        "paused": bool(getattr(ws, "paused", False)),
        "note": "Question is awaiting human input.",
    })
    return _compact_json(payload), False


def _resolve_architect_pending_question_engineer(
        state, architect_id: str, engineer_ident: str):
    """Resolve the hired engineer whose pending blocking ask this architect may
    answer.

    Mirrors the gating of ``_architect_engineer_pending_question_json`` (the
    read surface) so the answer affordance accepts exactly the asks the
    architect can see: the engineer must be hired by this architect and have an
    actor-stamped pending question in its group.  Returns
    ``(engineer, group, question, error)``.
    """
    engineer_id, engineer_error = _resolve_architect_hired_engineer(
        state, architect_id, engineer_ident
    )
    if not engineer_id:
        return None, "", "", engineer_error
    engineer = state.agents.get(engineer_id)
    group = str(getattr(engineer, "group", "") or "").strip()
    if not group:
        return engineer, "", "", "Engineer has no group"
    ws = state.get_engineer_settings(group)
    question = str(getattr(ws, "pending_question", "") or "").strip()
    actor_id = str(getattr(ws, "pending_question_actor_id", "") or "").strip()
    if not question or actor_id != engineer_id:
        worker_ask = _open_worker_ask_for_engineer(state, engineer_id)
        if worker_ask:
            return engineer, group, "", (
                "This is a worker-level ask, not an engineer-level pending "
                "question. The owning Engineer must resolve it with "
                "agent_ask_answer(task="
                f"{str(getattr(worker_ask, 'id', '') or '')})."
            )
        return engineer, group, "", "No pending blocking question for that engineer"
    return engineer, group, question, ""
