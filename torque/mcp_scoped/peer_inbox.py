"""Architect/Engineer peer inboxes, thread inspection, and semantic recall."""

import hashlib
import json

from torque.ai_recall import normalize_recall_limit, semantic_recall_payload
from torque.mcp_scoped.architect_activity import _peer_row_involves_engineer
from torque.mcp_scoped.architect_reports import _compact_json
from torque.mcp_scoped.common import (
    _agent_is_tombstoned,
    _agent_peer_message_row_to_entry,
    _filter_tasks_for_caller,
    _is_engineer_like_cell,
    _optional_bool_arg,
    _resolve_architect_hired_engineer,
    _thread_requires_architect_reply,
    authorize_caller,
)
from torque.mcp_scoped.peer_context import (
    _engineer_peer_hiring_architect_id,
    _resolve_architect_peer_filter,
    _resolve_engineer_peer,
    _resolve_engineer_peer_filter,
)
from torque.mcp_scoped.proposals import (
    _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT,
    _ARCHITECT_PEER_INBOX_MAX_LIMIT,
)
from torque.server_artifacts import serialize_task_for_mcp

_ARCHITECT_PEER_MESSAGE_LENGTH_LIMIT = 16 * 1024

def _validate_architect_peer_message_length(
        message: str,
        context_summary: str = "") -> str:
    size = len(str(message or "").encode("utf-8")) + len(
        str(context_summary or "").encode("utf-8")
    )
    if size > _ARCHITECT_PEER_MESSAGE_LENGTH_LIMIT:
        return (
            "message and context_summary must be at most "
            f"{_ARCHITECT_PEER_MESSAGE_LENGTH_LIMIT} bytes combined"
        )
    return ""


def _peer_message_id_from_idempotency_key(idempotency_key: str) -> str:
    key = str(idempotency_key or "").strip()
    if not key:
        return ""
    digest = hashlib.sha256(
        ("agent-peer-message\0" + key).encode("utf-8")
    ).hexdigest()
    return "msg-" + digest[:12]


def _architect_peer_inbox_json(
        state,
        caller_id: str,
        args: dict) -> tuple[str, bool]:
    try:
        limit = int(args.get("limit", _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT)
                    or _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT
    limit = max(1, min(limit, _ARCHITECT_PEER_INBOX_MAX_LIMIT))
    try:
        since_value = float(args.get("since", 0) or 0)
    except (TypeError, ValueError):
        return "since must be a number", True
    requires_reply, requires_reply_error = _optional_bool_arg(
        args,
        "requires_reply",
        False,
    )
    if requires_reply_error:
        return requires_reply_error, True
    peer_id, peer_error = _resolve_architect_peer_filter(
        state,
        caller_id,
        str(args.get("peer_architect_id", "") or "").strip(),
    )
    if peer_error:
        return peer_error, True
    thread_id = str(args.get("thread_id", "") or "").strip()
    if not getattr(state, "db", None):
        return _compact_json({"type": "architect_peer_inbox", "threads": []}), False
    row_limit = min(max(limit * 20, limit), 1000)
    rows = state.db.load_agent_peer_messages_for_agent(
        caller_id,
        limit=row_limit,
        since=since_value,
        peer_id=peer_id,
        thread_id=thread_id,
    )
    rows = [
        row for row in rows
        if {
            str(row.get("sender_kind", "") or "").strip(),
            str(row.get("recipient_kind", "") or "").strip(),
        } == {"architect"}
    ]
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("thread_id", "") or ""), []).append(row)
    threads = []
    for group_thread_id, messages in grouped.items():
        messages.sort(
            key=lambda row: (
                float(row.get("created_at", 0) or 0),
                str(row.get("id", "") or ""),
            )
        )
        requires = _thread_requires_architect_reply(messages, caller_id)
        if requires_reply and not requires:
            continue
        last = messages[-1]
        entry_for_caller = _agent_peer_message_row_to_entry(last, caller_id)
        peer_architect_id = entry_for_caller["peer_id"]
        peer = state.agents.get(peer_architect_id)
        threads.append({
            "thread_id": group_thread_id,
            "peer_architect_id": peer_architect_id,
            "peer_name": str(getattr(peer, "name", "") or "").strip()
            or peer_architect_id,
            "last_message_at": float(last.get("created_at", 0) or 0),
            "requires_reply": requires,
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
    return _compact_json({
        "type": "architect_peer_inbox",
        "threads": threads[:limit],
    }), False


def _is_engineer_peer_row(row: dict) -> bool:
    return (
        str((row or {}).get("sender_kind", "") or "").strip() == "engineer"
        and str((row or {}).get("recipient_kind", "") or "").strip() == "engineer"
    )


def _engineer_peer_thread_ids(rows: list[dict]) -> set[str]:
    ids: set[str] = set()
    for row in rows or []:
        if not _is_engineer_peer_row(row):
            continue
        for field in ("sender_id", "recipient_id"):
            value = str((row or {}).get(field, "") or "").strip()
            if value:
                ids.add(value)
    return ids


def _thread_pair_key_for_rows(rows: list[dict]) -> str:
    ids = sorted(_engineer_peer_thread_ids(rows))
    if len(ids) != 2:
        return ""
    return f"agent-pair:{ids[0]}:{ids[1]}"


def _thread_context_from_rows(rows: list[dict]) -> dict:
    task_ids: list[str] = []
    engineer_ids: list[str] = []
    decision_ids: list[str] = []
    summaries: list[str] = []
    snapshots: list[dict] = []
    for row in rows or []:
        for source, target in (
                (row.get("context_task_ids", []) or [], task_ids),
                (row.get("context_engineer_ids", []) or [], engineer_ids),
                (row.get("context_decision_ids", []) or [], decision_ids)):
            for value in source:
                text = str(value or "").strip()
                if text and text not in target:
                    target.append(text)
        summary = str(row.get("context_summary", "") or "").strip()
        if summary and summary not in summaries:
            summaries.append(summary)
        snapshot = dict(row.get("context_snapshot", {}) or {})
        if snapshot:
            snapshots.append(snapshot)
    merged_snapshot: dict = {"tasks": [], "streams": []}
    inspect_grant = {}
    seen_task_ids: set[str] = set()
    seen_stream_keys: set[str] = set()
    for snapshot in snapshots:
        if not inspect_grant and isinstance(snapshot.get("inspect_grant"), dict):
            inspect_grant = dict(snapshot.get("inspect_grant") or {})
        for task in snapshot.get("tasks", []) or []:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("id", "") or "").strip()
            key = task_id or json.dumps(task, sort_keys=True)
            if key in seen_task_ids:
                continue
            seen_task_ids.add(key)
            merged_snapshot["tasks"].append(dict(task))
        for stream in snapshot.get("streams", []) or []:
            if not isinstance(stream, dict):
                continue
            key = (
                str(stream.get("stream_id", "") or ""),
                str(stream.get("repo_root", "") or ""),
                str(stream.get("branch", "") or ""),
            )
            if key in seen_stream_keys:
                continue
            seen_stream_keys.add(key)
            merged_snapshot["streams"].append(dict(stream))
    if inspect_grant:
        merged_snapshot["inspect_grant"] = inspect_grant
    return {
        "task_ids": task_ids,
        "engineer_ids": engineer_ids,
        "decision_ids": decision_ids,
        "summary": "\n".join(summaries),
        "snapshot": merged_snapshot,
    }


def _engineer_peer_live_context(state, rows: list[dict], *,
                                include_live: bool = True) -> dict:
    context = _thread_context_from_rows(rows)
    if not include_live:
        return {"tasks": [], "streams": [], "live_unavailable_reason": "disabled"}
    group = ""
    for row in rows or []:
        group = str(row.get("group_name", row.get("group", "")) or "").strip()
        if group:
            break
    live_tasks = []
    unavailable: list[str] = []
    for task_id in context.get("task_ids", []) or []:
        task = state.board_tasks.get(str(task_id or "").strip())
        if not task or (group and str(getattr(task, "group", "") or "").strip() != group):
            unavailable.append(str(task_id or "").strip())
            continue
        live_tasks.append(serialize_task_for_mcp(task, tasks_by_id=state.board_tasks))
    live = {
        "tasks": live_tasks,
        "streams": list((context.get("snapshot", {}) or {}).get("streams", []) or []),
    }
    if unavailable:
        live["live_unavailable_reason"] = "tasks unavailable: " + ", ".join(unavailable)
    return live


def _thread_requires_engineer_reply(messages: list[dict], caller_id: str) -> bool:
    return _thread_requires_architect_reply(messages, caller_id)


def _engineer_peer_thread_summary(state, messages: list[dict], caller_id: str = "") -> dict:
    messages = sorted(
        [dict(row) for row in messages if _is_engineer_peer_row(row)],
        key=lambda row: (
            float(row.get("created_at", 0) or 0),
            str(row.get("id", "") or ""),
        ),
    )
    if not messages:
        return {}
    last = messages[-1]
    participant_ids = sorted(_engineer_peer_thread_ids(messages))
    participants = []
    for participant_id in participant_ids:
        cell = state.agents.get(participant_id)
        participants.append({
            "id": participant_id,
            "name": str(getattr(cell, "name", "") or participant_id),
            "slug": str(getattr(cell, "slug", "") or ""),
            "kind": str(getattr(cell, "kind", "") or "engineer"),
            "hired_by_architect_id": _engineer_peer_hiring_architect_id(cell),
        })
    context = _thread_context_from_rows(messages)
    thread_id = str(last.get("thread_id", "") or "").strip()
    return {
        "thread_id": thread_id,
        "pair_thread_id": _thread_pair_key_for_rows(messages),
        "participants": participants,
        "participant_ids": participant_ids,
        "last_message_at": float(last.get("created_at", 0) or 0),
        "last_message_id": str(last.get("id", "") or ""),
        "last_message": _agent_peer_message_row_to_entry(
            last,
            caller_id or str(last.get("recipient_id", "") or ""),
        ),
        "message_count": len(messages),
        "requires_reply": (
            _thread_requires_engineer_reply(messages, caller_id)
            if caller_id else False
        ),
        "ack_required_count": sum(
            1 for row in messages if bool(row.get("ack_required", False))
        ),
        "delivery": {
            "buffered": sum(
                1 for row in messages
                if str(row.get("delivery_state", "") or "buffered") == "buffered"
            ),
            "failed": sum(
                1 for row in messages
                if str(row.get("delivery_state", "") or "") == "failed"
            ),
        },
        "context": context,
    }


def _engineer_peer_inbox_json(
        state,
        caller_id: str,
        args: dict) -> tuple[str, bool]:
    try:
        limit = int(args.get("limit", _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT)
                    or _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT
    limit = max(1, min(limit, _ARCHITECT_PEER_INBOX_MAX_LIMIT))
    try:
        since_value = float(args.get("since", 0) or 0)
    except (TypeError, ValueError):
        return "since must be a number", True
    requires_reply, requires_reply_error = _optional_bool_arg(
        args,
        "requires_reply",
        False,
    )
    if requires_reply_error:
        return requires_reply_error, True
    peer_id, peer_error = _resolve_engineer_peer_filter(
        state,
        caller_id,
        str(args.get("peer_engineer_id", "") or "").strip(),
    )
    if peer_error:
        return peer_error, True
    db = getattr(state, "db", None)
    if not db:
        return _compact_json({"type": "engineer_peer_inbox", "threads": []}), False
    row_limit = min(max(limit * 20, limit), 1000)
    loader = getattr(db, "load_engineer_peer_messages_for_agent", None)
    rows = (
        loader(
            caller_id,
            limit=row_limit,
            since=since_value,
            peer_id=peer_id,
            thread_id=str(args.get("thread_id", "") or "").strip(),
        )
        if callable(loader)
        else []
    )
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("thread_id", "") or ""), []).append(row)
    threads = []
    for messages in grouped.values():
        messages.sort(
            key=lambda row: (
                float(row.get("created_at", 0) or 0),
                str(row.get("id", "") or ""),
            )
        )
        requires = _thread_requires_engineer_reply(messages, caller_id)
        if requires_reply and not requires:
            continue
        summary = _engineer_peer_thread_summary(state, messages, caller_id)
        if summary:
            summary["messages"] = [
                _agent_peer_message_row_to_entry(row, caller_id)
                for row in messages
            ]
            threads.append(summary)
    threads.sort(
        key=lambda item: (
            float(item.get("last_message_at", 0) or 0),
            str(item.get("thread_id", "") or ""),
        ),
        reverse=True,
    )
    return _compact_json({
        "type": "engineer_peer_inbox",
        "threads": threads[:limit],
    }), False


def _engineer_peer_thread_rows_for_inspect(
        state,
        caller_kind: str,
        caller_id: str,
        args: dict,
        *,
        limit: int = 1000) -> tuple[list[dict], str]:
    db = getattr(state, "db", None)
    if not db:
        return [], "thread not found in scope"
    message_id = str(args.get("message_id", "") or "").strip()
    thread_id = str(args.get("thread_id", "") or "").strip()
    if not message_id and not thread_id:
        return [], "message_id or thread_id is required"
    if message_id:
        row = db.load_agent_peer_message(message_id)
        if not row or not _is_engineer_peer_row(row):
            return [], "thread not found in scope"
        thread_id = str(row.get("thread_id", "") or "").strip()
    loader = getattr(db, "load_engineer_peer_messages_for_thread", None)
    if not callable(loader):
        return [], "thread not found in scope"
    if caller_kind == "engineer":
        rows = loader(thread_id, engineer_id=caller_id, limit=limit)
    else:
        rows = loader(thread_id, limit=limit)
    rows = [row for row in rows if _is_engineer_peer_row(row)]
    if not rows:
        return [], "thread not found in scope"
    return rows, ""


def _engineer_peer_thread_belongs_to_pair(
        state,
        thread_id: str,
        sender_id: str,
        recipient_id: str) -> tuple[bool, str]:
    thread_id = str(thread_id or "").strip()
    if not thread_id:
        return True, ""
    expected = {
        str(sender_id or "").strip(),
        str(recipient_id or "").strip(),
    }
    if len(expected) != 2 or not all(expected):
        return False, "thread not found in scope"
    db = getattr(state, "db", None)
    loader = getattr(db, "load_engineer_peer_messages_for_thread", None)
    if not callable(loader):
        return False, "thread not found in scope"
    rows = loader(thread_id, limit=5000)
    if not rows:
        return False, "thread not found in scope"
    participants = _engineer_peer_thread_ids(rows)
    if participants != expected:
        return False, "thread not found in scope"
    return True, ""


def _engineer_peer_existing_message_matches_pair(
        row: dict,
        sender_id: str,
        recipient_id: str,
        requested_thread_id: str = "") -> bool:
    if not _is_engineer_peer_row(row):
        return False
    participants = {
        str((row or {}).get("sender_id", "") or "").strip(),
        str((row or {}).get("recipient_id", "") or "").strip(),
    }
    expected = {
        str(sender_id or "").strip(),
        str(recipient_id or "").strip(),
    }
    if participants != expected:
        return False
    requested_thread_id = str(requested_thread_id or "").strip()
    existing_thread_id = str((row or {}).get("thread_id", "") or "").strip()
    if requested_thread_id and existing_thread_id != requested_thread_id:
        return False
    return True


def _engineer_peer_inspect_json(
        state,
        caller_id: str,
        args: dict) -> tuple[str, bool]:
    include_live, live_error = _optional_bool_arg(args, "include_live", True)
    if live_error:
        return live_error, True
    rows, error = _engineer_peer_thread_rows_for_inspect(
        state,
        "engineer",
        caller_id,
        args,
        limit=1000,
    )
    if error:
        return error, True
    messages = [
        _agent_peer_message_row_to_entry(row, caller_id)
        for row in sorted(rows, key=lambda row: (
            float(row.get("created_at", 0) or 0),
            str(row.get("id", "") or ""),
        ))
    ]
    summary = _engineer_peer_thread_summary(state, rows, caller_id)
    return _compact_json({
        "type": "engineer_peer_inspect",
        "thread_id": summary.get("thread_id", ""),
        "pair_thread_id": summary.get("pair_thread_id", ""),
        "participants": summary.get("participants", []),
        "messages": messages,
        "context": _thread_context_from_rows(rows),
        "live": _engineer_peer_live_context(
            state,
            rows,
            include_live=include_live,
        ),
    }), False


def _architect_can_inspect_engineer_peer_thread(
        state,
        architect_id: str,
        rows: list[dict]) -> bool:
    architect_id = str(architect_id or "").strip()
    participant_ids = _engineer_peer_thread_ids(rows)
    if len(participant_ids) != 2:
        return False
    for engineer_id in participant_ids:
        engineer = state.agents.get(engineer_id)
        if (
                not engineer
                or _agent_is_tombstoned(state, engineer)
                or not _is_engineer_like_cell(state, engineer)
                or _engineer_peer_hiring_architect_id(engineer) != architect_id):
            return False
    return True


def _architect_engineer_peer_threads_json(
        state,
        caller_id: str,
        args: dict) -> tuple[str, bool]:
    try:
        limit = int(args.get("limit", _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT)
                    or _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = _ARCHITECT_PEER_INBOX_DEFAULT_LIMIT
    limit = max(1, min(limit, _ARCHITECT_PEER_INBOX_MAX_LIMIT))
    try:
        active_since = float(args.get("active_since", 0) or 0)
    except (TypeError, ValueError):
        return "active_since must be a number", True
    engineer_filter = ""
    if str(args.get("engineer_id", "") or "").strip():
        engineer_filter, engineer_error = _resolve_architect_hired_engineer(
            state,
            caller_id,
            str(args.get("engineer_id", "") or "").strip(),
            include_tombstoned=True,
        )
        if not engineer_filter:
            return engineer_error, True
    db = getattr(state, "db", None)
    if not db:
        return _compact_json({"type": "engineer_peer_threads", "threads": []}), False
    thread_filter = str(args.get("thread_id", "") or "").strip()
    if thread_filter:
        rows = db.load_engineer_peer_messages_for_thread(thread_filter, limit=1000)
    else:
        caller = state.agents.get(str(caller_id or "").strip())
        group = str(getattr(caller, "group", "") or "").strip()
        rows = [
            row for row in db.load_recent_agent_peer_messages_for_group(
                group,
                limit=1000,
            )
            if _is_engineer_peer_row(row)
        ]
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("thread_id", "") or ""), []).append(row)
    threads = []
    for messages in grouped.values():
        messages.sort(
            key=lambda row: (
                float(row.get("created_at", 0) or 0),
                str(row.get("id", "") or ""),
            )
        )
        if not _architect_can_inspect_engineer_peer_thread(
                state,
                caller_id,
                messages):
            continue
        if engineer_filter and engineer_filter not in _engineer_peer_thread_ids(messages):
            continue
        summary = _engineer_peer_thread_summary(state, messages)
        if active_since and float(summary.get("last_message_at", 0) or 0) <= active_since:
            continue
        threads.append(summary)
    threads.sort(
        key=lambda item: (
            float(item.get("last_message_at", 0) or 0),
            str(item.get("thread_id", "") or ""),
        ),
        reverse=True,
    )
    return _compact_json({
        "type": "engineer_peer_threads",
        "threads": threads[:limit],
    }), False


def _architect_engineer_peer_inspect_json(
        state,
        caller_id: str,
        args: dict) -> tuple[str, bool]:
    include_live, live_error = _optional_bool_arg(args, "include_live", True)
    if live_error:
        return live_error, True
    try:
        limit = int(args.get("limit", 100) or 100)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 1000))
    rows, error = _engineer_peer_thread_rows_for_inspect(
        state,
        "architect",
        caller_id,
        args,
        limit=1000,
    )
    if error or not _architect_can_inspect_engineer_peer_thread(
            state,
            caller_id,
            rows):
        return "thread not found in scope", True
    rows = sorted(rows, key=lambda row: (
        float(row.get("created_at", 0) or 0),
        str(row.get("id", "") or ""),
    ))
    summary = _engineer_peer_thread_summary(state, rows)
    return _compact_json({
        "type": "engineer_peer_inspect",
        "thread_id": summary.get("thread_id", ""),
        "pair_thread_id": summary.get("pair_thread_id", ""),
        "participants": summary.get("participants", []),
        "messages": [
            _agent_peer_message_row_to_entry(row, caller_id)
            for row in rows[-limit:]
        ],
        "context": _thread_context_from_rows(rows),
        "live": _engineer_peer_live_context(
            state,
            rows,
            include_live=include_live,
        ),
    }), False


def _candidate_participants(candidate) -> set[str]:
    return {
        str(value or "").strip()
        for value in getattr(candidate, "participant_ids", ()) or ()
        if str(value or "").strip()
    }


def _candidate_visibility(candidate) -> dict:
    value = getattr(candidate, "visibility_json", {}) or {}
    return dict(value) if isinstance(value, dict) else {}


def _candidate_group(candidate) -> str:
    return str(getattr(candidate, "group_name", "") or "").strip()


def _candidate_source_type(candidate) -> str:
    return str(getattr(candidate, "source_type", "") or "").strip()


def _candidate_source_id(candidate) -> str:
    return str(getattr(candidate, "source_id", "") or "").strip()


def _candidate_owner_id(candidate) -> str:
    return str(getattr(candidate, "owner_id", "") or "").strip()


def _engineer_peer_thread_rows_for_recall(state, thread_id: str) -> list[dict]:
    db = getattr(state, "db", None)
    loader = getattr(db, "load_engineer_peer_messages_for_thread", None)
    if not callable(loader):
        return []
    rows = loader(str(thread_id or "").strip(), limit=1000)
    return [row for row in rows if _is_engineer_peer_row(row)]


def _engineer_can_recall_peer_thread(state, caller_id: str, candidate) -> bool:
    thread_id = _candidate_source_id(candidate)
    if not thread_id:
        return False

    # Participant access goes through the same participant-scoped inspect
    # loader used by engineer_peer_inspect.
    rows, error = _engineer_peer_thread_rows_for_inspect(
        state,
        "engineer",
        caller_id,
        {"thread_id": thread_id},
        limit=1000,
    )
    if not error and rows:
        return True

    # Non-participant access is limited to the inspect-granted context path:
    # the caller must be hired by the same Architect as both thread
    # participants, the persisted inspect_grant must name that Architect, and
    # the thread context must involve the caller via the existing helper.
    rows = _engineer_peer_thread_rows_for_recall(state, thread_id)
    if not rows:
        return False
    caller = state.agents.get(str(caller_id or "").strip())
    caller_architect_id = _engineer_peer_hiring_architect_id(caller)
    if not caller_architect_id:
        return False
    metadata_architect_ids = {
        str(value or "").strip()
        for value in (
            _candidate_visibility(candidate).get(
                "participant_hired_by_architect_ids",
                [],
            )
            or []
        )
        if str(value or "").strip()
    }
    if metadata_architect_ids and caller_architect_id not in metadata_architect_ids:
        return False
    context = _thread_context_from_rows(rows)
    inspect_grant = dict(
        ((context.get("snapshot", {}) or {}).get("inspect_grant", {}) or {})
    )
    if (
        str(inspect_grant.get("supervising_architect_id", "") or "").strip()
        != caller_architect_id
    ):
        return False
    if not any(_peer_row_involves_engineer(state, row, caller_id) for row in rows):
        return False
    participant_ids = _engineer_peer_thread_ids(rows)
    if len(participant_ids) != 2 or str(caller_id or "").strip() in participant_ids:
        return False
    for participant_id in participant_ids:
        resolved_id, filter_error = _resolve_engineer_peer_filter(
            state,
            caller_id,
            participant_id,
        )
        if filter_error or resolved_id != participant_id:
            return False
        peer, peer_error = _resolve_engineer_peer(
            state,
            caller_id,
            participant_id,
            include_dismissed=True,
        )
        if peer_error or not peer:
            return False
    return True


def _engineer_recall_candidate_visible(
        state,
        caller_id: str,
        caller_group: str,
        candidate) -> bool:
    source_type = _candidate_source_type(candidate)
    source_group = _candidate_group(candidate)
    if source_group and source_group != caller_group:
        return False
    if source_type == "task":
        return _candidate_source_id(candidate) in _filter_tasks_for_caller(
            state,
            "engineer",
            caller_id,
        )
    if source_type == "engineer_journal":
        owner_id = _candidate_owner_id(candidate)
        return (
            owner_id == str(caller_id or "").strip()
            or (
                str(getattr(candidate, "owner_kind", "") or "").strip() == "group"
                and bool(source_group)
                and source_group == caller_group
                and not owner_id
            )
        )
    if source_type == "engineer_peer_thread":
        return _engineer_can_recall_peer_thread(state, caller_id, candidate)
    if source_type == "decision":
        return str(caller_id or "").strip() in _candidate_participants(candidate)
    return False


def _architect_can_recall_peer_thread(state, caller_id: str, candidate) -> bool:
    thread_id = _candidate_source_id(candidate)
    if not thread_id:
        return False
    caller_architect_id = str(caller_id or "").strip()
    metadata_architect_ids = {
        str(value or "").strip()
        for value in (
            _candidate_visibility(candidate).get(
                "participant_hired_by_architect_ids",
                [],
            )
            or []
        )
        if str(value or "").strip()
    }
    if metadata_architect_ids and caller_architect_id not in metadata_architect_ids:
        return False
    _text, is_error = _architect_engineer_peer_inspect_json(
        state,
        caller_id,
        {"thread_id": thread_id, "include_live": False},
    )
    return not is_error


def _architect_recall_candidate_visible(
        state,
        caller_id: str,
        caller_group: str,
        candidate) -> bool:
    source_type = _candidate_source_type(candidate)
    source_group = _candidate_group(candidate)
    if source_group and source_group != caller_group:
        return False
    if source_type == "architect_journal":
        return _candidate_owner_id(candidate) == str(caller_id or "").strip()
    if source_type == "decision":
        return _candidate_owner_id(candidate) == str(caller_id or "").strip()
    if source_type == "task":
        return _candidate_source_id(candidate) in _filter_tasks_for_caller(
            state,
            "architect",
            caller_id,
        )
    if source_type == "engineer_journal":
        owner_id = _candidate_owner_id(candidate)
        if not owner_id:
            return False
        engineer_id, _error = _resolve_architect_hired_engineer(
            state,
            caller_id,
            owner_id,
            include_tombstoned=True,
        )
        return engineer_id == owner_id
    if source_type == "engineer_peer_thread":
        return _architect_can_recall_peer_thread(state, caller_id, candidate)
    return False


async def _semantic_recall_json(
        state,
        caller_kind: str,
        caller_id: str,
        args: dict) -> tuple[str, bool]:
    _cell, caller_group, _kind, auth_error, auth_structured = authorize_caller(
        state,
        caller_kind=caller_kind,
        caller_id=caller_id,
    )
    if auth_error:
        return auth_error, auth_structured
    query = str(args.get("query", "") or "").strip()
    if not query:
        return "query is required", True
    limit, limit_error = normalize_recall_limit(args.get("limit"))
    if limit_error:
        return limit_error, True
    if caller_kind == "architect":
        visible = lambda candidate: _architect_recall_candidate_visible(
            state,
            caller_id,
            caller_group,
            candidate,
        )
    else:
        visible = lambda candidate: _engineer_recall_candidate_visible(
            state,
            caller_id,
            caller_group,
            candidate,
        )
    try:
        payload = await semantic_recall_payload(
            state=state,
            query=query,
            limit=limit,
            visibility_filter=visible,
        )
    except ValueError as exc:
        return str(exc), True
    payload["caller_kind"] = caller_kind
    return _compact_json(payload), False
