"""SQLite persistence for agent audit history, worklogs, and messages."""

from __future__ import annotations

import json
import time
from typing import Optional

from ..db_schema import AGENT_PEER_MESSAGE_COLUMNS
from .common import snapshot_db_payload as _snapshot_db_payload


_AGENT_PEER_MESSAGE_COLUMNS = list(AGENT_PEER_MESSAGE_COLUMNS)
_AGENT_PEER_MESSAGE_COLUMNS.remove("idempotency_key")
_AGENT_PEER_MESSAGE_COLUMNS.insert(3, "idempotency_key")
_AGENT_PEER_MESSAGE_JSON_LIST_FIELDS = (
    "context_task_ids",
    "context_engineer_ids",
    "context_decision_ids",
)
_AGENT_PEER_MESSAGE_DELIVERY_STATES = {"buffered", "delivered", "failed"}
_AGENT_PEER_MESSAGE_NON_USER_WHERE = (
    "(sender_kind!='user' AND recipient_kind!='user' "
    "AND message_type='message' AND blocking=0)"
)
# Only actual user participants belong to the user↔agent DM projection.
# A non-user blocking raise is persisted in this table for routing/audit but
# must not leak into local snapshots, deltas, or remote user egress.
_USER_PARTICIPANT_WHERE = (
    "((sender_kind='user' AND sender_id='user') "
    "OR (recipient_kind='user' AND recipient_id='user'))"
)
# Blocking raise mirrors are special: only resolver-stamped user destinations
# are user-DM rows. Existing non-raise system/audit display rows retain their
# established projection behavior.
_AGENT_DIRECT_MESSAGE_WHERE = (
    f"({_USER_PARTICIPANT_WHERE} "
    "OR (message_type NOT IN ('ask','ask_reply') "
    "AND (message_type!='message' OR blocking!=0)))"
)
_AGENT_PEER_MESSAGE_USER_WHERE = _AGENT_DIRECT_MESSAGE_WHERE
# Buffered replies to a parent-routed raise still need durable replay to the
# asking agent, even though neither participant is the user.
_BUFFERED_DIRECT_MESSAGE_TRANSPORT_WHERE = (
    f"({_AGENT_DIRECT_MESSAGE_WHERE} OR message_type='ask_reply')"
)
_AGENT_PEER_CHAT_WHERE = (
    f"({_AGENT_PEER_MESSAGE_NON_USER_WHERE} "
    "AND sender_kind IN ('architect','engineer') "
    "AND recipient_kind IN ('architect','engineer'))"
)
_ENGINEER_PEER_MESSAGE_WHERE = (
    f"({_AGENT_PEER_MESSAGE_NON_USER_WHERE} "
    "AND sender_kind='engineer' AND recipient_kind='engineer')"
)


def _json_text_list(value) -> list[str]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value or "[]")
        except (json.JSONDecodeError, TypeError):
            decoded = []
    else:
        decoded = value
    if not isinstance(decoded, list):
        return []
    out: list[str] = []
    for item in decoded:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _json_text_dict(value) -> dict:
    if isinstance(value, str):
        try:
            decoded = json.loads(value or "{}")
        except (json.JSONDecodeError, TypeError):
            decoded = {}
    else:
        decoded = value
    return decoded if isinstance(decoded, dict) else {}


def _peer_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default or 0.0)


def _peer_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


def canonical_user_agent_thread_id(agent_id: str, *, user_id: str = "user") -> str:
    """Return the canonical V1 thread id for one user↔agent lane.

    The V1 direct-message panel renders one conversation per viewed agent.
    Rows involving the synthetic user participant are therefore grouped under
    this stable thread id.  Agent↔agent peer messages keep their existing
    caller-supplied/default message-id threads.
    """
    aid = str(agent_id or "").strip()
    uid = str(user_id or "user").strip() or "user"
    if not aid:
        return ""
    return f"user-agent:{uid}:{aid}"


def _direct_message_thread_id_for_participants(source: dict) -> str:
    sender_kind = str(source.get("sender_kind", "") or "").strip()
    recipient_kind = str(source.get("recipient_kind", "") or "").strip()
    if sender_kind == "user" and recipient_kind != "user":
        return canonical_user_agent_thread_id(
            source.get("recipient_id", ""),
            user_id=str(source.get("sender_id", "") or "user"),
        )
    if recipient_kind == "user" and sender_kind != "user":
        return canonical_user_agent_thread_id(
            source.get("sender_id", ""),
            user_id=str(source.get("recipient_id", "") or "user"),
        )
    return ""


def _normalize_agent_peer_message_record(record: dict) -> dict:
    source = dict(record or {})
    message_id = str(source.get("id", "") or "").strip()
    if not message_id:
        raise ValueError("id is required")
    message = str(source.get("message", "") or "")
    if not message:
        raise ValueError("message is required")
    sender_id = str(source.get("sender_id", "") or "").strip()
    recipient_id = str(source.get("recipient_id", "") or "").strip()
    if not sender_id:
        raise ValueError("sender_id is required")
    if not recipient_id:
        raise ValueError("recipient_id is required")
    created_at = _peer_float(
        source.get("created_at", source.get("timestamp", time.time())),
        time.time(),
    )
    delivery_state = str(
        source.get("delivery_state", "buffered") or "buffered"
    ).strip()
    if delivery_state not in _AGENT_PEER_MESSAGE_DELIVERY_STATES:
        delivery_state = "buffered"
    direct_thread_id = _direct_message_thread_id_for_participants(source)
    thread_id = direct_thread_id or str(source.get("thread_id", "") or "").strip()
    if not thread_id:
        thread_id = message_id
    group_name = str(
        source.get("group_name", source.get("group", "")) or ""
    ).strip()
    context_snapshot = _json_text_dict(source.get("context_snapshot", {}))
    return {
        "id": message_id,
        "thread_id": thread_id,
        "reply_to_id": str(source.get("reply_to_id", "") or "").strip(),
        "idempotency_key": str(source.get("idempotency_key", "") or "").strip(),
        "group_name": group_name,
        "sender_id": sender_id,
        "sender_kind": str(
            source.get("sender_kind", "architect") or "architect"
        ).strip(),
        "sender_name": str(source.get("sender_name", "") or ""),
        "recipient_id": recipient_id,
        "recipient_kind": str(
            source.get("recipient_kind", "architect") or "architect"
        ).strip(),
        "recipient_name": str(source.get("recipient_name", "") or ""),
        "message": message,
        "message_type": str(
            source.get("message_type", "message") or "message"
        ).strip() or "message",
        "created_at": created_at,
        "ack_required": _peer_bool(source.get("ack_required", False)),
        "blocking": _peer_bool(source.get("blocking", False)),
        "source_task_id": str(source.get("source_task_id", "") or "").strip(),
        "context_task_ids": _json_text_list(
            source.get("context_task_ids", [])
        ),
        "context_engineer_ids": _json_text_list(
            source.get("context_engineer_ids", [])
        ),
        "context_decision_ids": _json_text_list(
            source.get("context_decision_ids", [])
        ),
        "context_summary": str(source.get("context_summary", "") or ""),
        "context_snapshot": context_snapshot,
        "delivery_state": delivery_state,
        "delivery_reason": str(source.get("delivery_reason", "") or ""),
        "delivered_at": _peer_float(source.get("delivered_at", 0), 0),
        "read_at": _peer_float(source.get("read_at", 0), 0),
        "archived_at": _peer_float(source.get("archived_at", 0), 0),
    }


def _agent_peer_message_insert_values(record: dict) -> tuple:
    normalized = _normalize_agent_peer_message_record(record)
    values = []
    for column in _AGENT_PEER_MESSAGE_COLUMNS:
        value = normalized[column]
        if column in _AGENT_PEER_MESSAGE_JSON_LIST_FIELDS:
            value = json.dumps(value, separators=(",", ":"))
        elif column == "context_snapshot":
            value = json.dumps(value, separators=(",", ":"), sort_keys=True)
        elif column in {"ack_required", "blocking"}:
            value = int(bool(value))
        values.append(value)
    return tuple(values)


def _decode_agent_peer_message_row(row, cols=None) -> dict:
    cols = cols or _AGENT_PEER_MESSAGE_COLUMNS
    decoded = dict(zip(cols, row))
    for field in _AGENT_PEER_MESSAGE_JSON_LIST_FIELDS:
        decoded[field] = _json_text_list(decoded.get(field, "[]"))
    decoded["context_snapshot"] = _json_text_dict(
        decoded.get("context_snapshot", "{}")
    )
    decoded["ack_required"] = _peer_bool(decoded.get("ack_required", 0))
    decoded["blocking"] = _peer_bool(decoded.get("blocking", 0))
    decoded["created_at"] = _peer_float(decoded.get("created_at", 0), 0)
    decoded["delivered_at"] = _peer_float(decoded.get("delivered_at", 0), 0)
    decoded["read_at"] = _peer_float(decoded.get("read_at", 0), 0)
    decoded["archived_at"] = _peer_float(decoded.get("archived_at", 0), 0)
    decoded["timestamp"] = decoded["created_at"]
    return decoded


def _mcp_dispatch_response_group(response: dict) -> str:
    """Best-effort group inference for persisted Engineer dispatch responses."""
    if not isinstance(response, dict):
        return ""
    for key in ("group", "group_name", "engineer_group"):
        value = str(response.get(key, "") or "").strip()
        if value:
            return value
    results = response.get("results")
    if not isinstance(results, list):
        return ""
    groups = {
        str(item.get("engineer_group", "") or item.get("group", "") or "").strip()
        for item in results
        if isinstance(item, dict)
        and str(item.get("engineer_group", "") or item.get("group", "") or "").strip()
    }
    return next(iter(groups)) if len(groups) == 1 else ""


def _decode_mcp_response_payload(response: dict) -> dict:
    """Return the tool JSON inside an MCP result wrapper when present."""
    if not isinstance(response, dict):
        return {}
    content = response.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            try:
                decoded = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(decoded, dict):
                return decoded
    return response


class AgentHistoryPersistenceMixin:
    """TorqueDB API for agent history, messages, and worklog persistence."""

    def save_engineer_task_log_entry(self, record: dict) -> int:
        """Insert a persisted Engineer dispatch/worklog row."""
        c = self._conn.execute(
            "INSERT INTO engineer_task_log "
            "(group_name, task_id, task_title, agent_id, agent_name, "
            "agent_slug, agent_owned, started_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                record["group"],
                record["task_id"],
                record.get("task_title", ""),
                record["agent_id"],
                record.get("agent_name", ""),
                record.get("agent_slug", ""),
                1 if record.get("agent_owned") else 0,
                record["started_at"],
            ),
        )
        self._conn.commit()
        return c.lastrowid

    def load_engineer_task_log(self, group_name: str, limit: int = 100) -> list[dict]:
        """Load recent persisted Engineer dispatch rows for a group."""
        rows = self._conn.execute(
            "SELECT id, group_name, task_id, task_title, agent_id, agent_name, "
            "agent_slug, agent_owned, started_at FROM engineer_task_log "
            "WHERE group_name=? ORDER BY started_at DESC, id DESC LIMIT ?",
            (group_name, limit),
        ).fetchall()
        return [
            {
                "id": row[0],
                "group": row[1],
                "task_id": row[2],
                "task_title": row[3],
                "agent_id": row[4],
                "agent_name": row[5],
                "agent_slug": row[6],
                "agent_owned": bool(row[7]),
                "started_at": row[8],
            }
            for row in rows
        ]

    def trim_engineer_task_log(self, group_name: str, limit: int = 200):
        """Trim persisted Engineer worklog rows for a group to ``limit``."""
        self._conn.execute(
            "DELETE FROM engineer_task_log WHERE group_name=? AND id NOT IN ("
            "SELECT id FROM engineer_task_log WHERE group_name=? "
            "ORDER BY started_at DESC, id DESC LIMIT ?"
            ")",
            (group_name, group_name, limit),
        )
        self._conn.commit()

    def rename_engineer_task_log_group(self, old_name: str, new_name: str):
        """Move persisted Engineer worklog rows to a renamed group."""
        self._conn.execute(
            "UPDATE engineer_task_log SET group_name=? WHERE group_name=?",
            (new_name, old_name),
        )
        self._conn.commit()

    def save_agent_history(self, record: dict):
        """Insert or replace an agent history record."""
        self._conn.execute("""
            INSERT OR REPLACE INTO agent_history
                (id, name, slug, "group", agent_type, template,
                 created_at, removed_at, worktree_branch,
                 total_tokens_in, total_tokens_out, total_tasks, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            record["id"], record["name"], record.get("slug", ""),
            record.get("group", ""), record.get("agent_type", ""),
            record.get("template", ""), record["created_at"],
            record.get("removed_at"), record.get("worktree_branch", ""),
            record.get("total_tokens_in", 0),
            record.get("total_tokens_out", 0),
            record.get("total_tasks", 0),
            record.get("status", "active"),
        ))
        self._conn.commit()

    def update_agent_history(self, agent_id: str, **fields):
        """Update specific fields on an agent history record."""
        if not fields:
            return
        allowed = {"name", "slug", "group", "agent_type", "template",
                   "removed_at", "worktree_branch", "total_tokens_in",
                   "total_tokens_out", "total_tasks", "status"}
        parts = []
        vals = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            col = f'"{k}"' if k == "group" else k
            parts.append(f"{col}=?")
            vals.append(v)
        if not parts:
            return
        vals.append(agent_id)
        self._conn.execute(
            f"UPDATE agent_history SET {','.join(parts)} WHERE id=?",
            vals)
        self._conn.commit()

    def save_agent_task(self, record: dict):
        """Insert an agent-task association."""
        self._conn.execute(
            "INSERT INTO agent_tasks "
            "(agent_id, task_id, task_title, started_at, completed_at, "
            "outcome) VALUES (?,?,?,?,?,?)",
            (record["agent_id"], record["task_id"],
             record["task_title"], record["started_at"],
             record.get("completed_at"), record.get("outcome", "")))
        self._conn.commit()

    def update_agent_task(self, agent_id: str, task_id: str, **fields):
        """Update an agent-task record (completed_at, outcome)."""
        parts = []
        vals = []
        for k in ("completed_at", "outcome"):
            if k in fields:
                parts.append(f"{k}=?")
                vals.append(fields[k])
        if not parts:
            return
        vals.extend([agent_id, task_id])
        self._conn.execute(
            f"UPDATE agent_tasks SET {','.join(parts)} "
            f"WHERE agent_id=? AND task_id=?", vals)
        self._conn.commit()

    def save_agent_message(self, record: dict):
        """Insert an agent message record."""
        self._conn.execute(
            "INSERT INTO agent_messages "
            "(agent_id, task_id, timestamp, action, message) "
            "VALUES (?,?,?,?,?)",
            (record["agent_id"], record.get("task_id", ""),
             record["timestamp"], record["action"],
             record.get("message", "")))
        self._conn.commit()

    def save_agent_peer_message(self, record: dict) -> dict:
        """Persist one canonical peer message.

        The message ID is the idempotency boundary. Re-saving the same
        deterministic ID is a no-op and returns the first stored row rather
        than overwriting delivery/thread metadata.
        """
        normalized = _normalize_agent_peer_message_record(record)
        values = _agent_peer_message_insert_values(normalized)
        placeholders = ",".join(["?"] * len(_AGENT_PEER_MESSAGE_COLUMNS))
        columns = ", ".join(_AGENT_PEER_MESSAGE_COLUMNS)
        self._conn.execute(
            "INSERT OR IGNORE INTO agent_peer_messages "
            f"({columns}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()
        return self.load_agent_peer_message(normalized["id"]) or {}

    def save_peer_message(self, record: dict) -> dict:
        """Compatibility alias for durable peer-message persistence."""
        return self.save_agent_peer_message(record)

    def save_direct_message(self, record: dict) -> dict:
        """Persist one direct/conversation message.

        V1 stores direct messages in the existing agent_peer_messages table.
        This named helper is the preferred API for user↔agent rows; legacy
        peer helpers remain wrappers over the same physical store.
        """
        return self.save_agent_peer_message(record)

    def load_agent_peer_message(self, message_id: str) -> dict | None:
        """Load one canonical peer message by ID."""
        message_id = str(message_id or "").strip()
        if not message_id:
            return None
        row = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE id=?",
            (message_id,),
        ).fetchone()
        if not row:
            return None
        return _decode_agent_peer_message_row(row)

    def load_peer_message(self, message_id: str) -> dict | None:
        """Compatibility alias for loading one peer message."""
        return self.load_agent_peer_message(message_id)

    def load_direct_message(self, message_id: str) -> dict | None:
        """Load one direct/conversation message by ID."""
        return self.load_agent_peer_message(message_id)

    def load_agent_peer_messages_for_agent(
        self,
        agent_id: str,
        *,
        limit: int = 100,
        since: float = 0,
        peer_id: str = "",
        thread_id: str = "",
        include_archived: bool = False,
    ) -> list[dict]:
        """Load recent peer messages involving one agent, newest first."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        where = [
            "(sender_id=? OR recipient_id=?)",
            _AGENT_PEER_MESSAGE_NON_USER_WHERE,
        ]
        params: list = [agent_id, agent_id]
        peer_id = str(peer_id or "").strip()
        if peer_id:
            where.append(
                "((sender_id=? AND recipient_id=?) OR "
                "(sender_id=? AND recipient_id=?))"
            )
            params.extend([agent_id, peer_id, peer_id, agent_id])
        thread_id = str(thread_id or "").strip()
        if thread_id:
            where.append("thread_id=?")
            params.append(thread_id)
        since_value = _peer_float(since, 0)
        if since_value > 0:
            where.append("created_at>?")
            params.append(since_value)
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def _agent_peer_thread_scope_query(
        self,
        agent_id: str,
        *,
        since: float = 0,
        peer_id: str = "",
        thread_id: str = "",
        sender_kind: str = "",
        recipient_kind: str = "",
        requires_reply: bool = False,
    ) -> tuple[str, list]:
        """Build the aggregate query used for bounded peer-inbox pages."""
        where = [
            "(sender_id=? OR recipient_id=?)",
            _AGENT_PEER_MESSAGE_NON_USER_WHERE,
            "archived_at=0",
        ]
        params: list = [agent_id, agent_id]
        if sender_kind:
            where.append("sender_kind=?")
            params.append(sender_kind)
        if recipient_kind:
            where.append("recipient_kind=?")
            params.append(recipient_kind)
        if peer_id:
            where.append(
                "((sender_id=? AND recipient_id=?) OR "
                "(sender_id=? AND recipient_id=?))"
            )
            params.extend([agent_id, peer_id, peer_id, agent_id])
        if thread_id:
            where.append("thread_id=?")
            params.append(thread_id)
        since_value = _peer_float(since, 0)
        if since_value > 0:
            where.append("created_at>?")
            params.append(since_value)
        query = (
            "SELECT thread_id, MAX(created_at) AS last_message_at, "
            "MAX(CASE WHEN sender_id=? THEN created_at ELSE 0 END) AS latest_outgoing, "
            "MAX(CASE WHEN sender_id<>? AND ack_required=1 THEN created_at ELSE 0 END) AS latest_incoming_ack "
            "FROM agent_peer_messages WHERE " + " AND ".join(where) + " GROUP BY thread_id"
        )
        # The expression is deliberately identical to
        # _thread_requires_architect_reply in the scoped layer.
        aggregate_params = [agent_id, agent_id, *params]
        if requires_reply:
            query += " HAVING latest_incoming_ack > latest_outgoing"
        return query, aggregate_params

    def load_agent_peer_threads_for_agent(
        self,
        agent_id: str,
        *,
        limit: int = 100,
        since: float = 0,
        peer_id: str = "",
        thread_id: str = "",
        sender_kind: str = "",
        recipient_kind: str = "",
        requires_reply: bool = False,
    ) -> list[dict]:
        """Return recent matching thread ids without materialising their messages."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        query, params = self._agent_peer_thread_scope_query(
            agent_id, since=since, peer_id=str(peer_id or "").strip(),
            thread_id=str(thread_id or "").strip(), sender_kind=sender_kind,
            recipient_kind=recipient_kind, requires_reply=requires_reply,
        )
        rows = self._conn.execute(
            query + " ORDER BY last_message_at DESC, thread_id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [
            {"thread_id": str(row[0] or ""), "last_message_at": float(row[1] or 0)}
            for row in rows
        ]

    def count_agent_peer_threads_for_agent(
        self,
        agent_id: str,
        **kwargs,
    ) -> int:
        """Count all matching peer threads, including pages not returned."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return 0
        query, params = self._agent_peer_thread_scope_query(agent_id, **kwargs)
        row = self._conn.execute(
            "SELECT COUNT(*) FROM (" + query + ")", params,
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def load_peer_messages_for_architect(
        self,
        architect_id: str,
        *,
        limit: int = 100,
        since: float = 0,
        peer_id: str = "",
        thread_id: str = "",
        include_archived: bool = False,
    ) -> list[dict]:
        """Load recent persisted peer messages for one Architect."""
        return self.load_agent_peer_messages_for_agent(
            architect_id,
            limit=limit,
            since=since,
            peer_id=peer_id,
            thread_id=thread_id,
            include_archived=include_archived,
        )

    def load_engineer_peer_messages_for_agent(
        self,
        engineer_id: str,
        *,
        limit: int = 100,
        since: float = 0,
        peer_id: str = "",
        thread_id: str = "",
        include_archived: bool = False,
    ) -> list[dict]:
        """Load recent Engineer↔Engineer peer rows involving one Engineer."""
        engineer_id = str(engineer_id or "").strip()
        if not engineer_id:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        where = [
            "(sender_id=? OR recipient_id=?)",
            _ENGINEER_PEER_MESSAGE_WHERE,
        ]
        params: list = [engineer_id, engineer_id]
        peer_id = str(peer_id or "").strip()
        if peer_id:
            where.append(
                "((sender_id=? AND recipient_id=?) OR "
                "(sender_id=? AND recipient_id=?))"
            )
            params.extend([engineer_id, peer_id, peer_id, engineer_id])
        thread_id = str(thread_id or "").strip()
        if thread_id:
            where.append("thread_id=?")
            params.append(thread_id)
        since_value = _peer_float(since, 0)
        if since_value > 0:
            where.append("created_at>?")
            params.append(since_value)
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_engineer_peer_messages_for_thread(
        self,
        thread_id: str,
        *,
        engineer_id: str = "",
        limit: int = 1000,
        include_archived: bool = False,
    ) -> list[dict]:
        """Load one Engineer↔Engineer peer thread oldest first."""
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return []
        limit = max(1, min(int(limit or 1000), 5000))
        where = ["thread_id=?", _ENGINEER_PEER_MESSAGE_WHERE]
        params: list = [thread_id]
        engineer_id = str(engineer_id or "").strip()
        if engineer_id:
            where.append("(sender_id=? OR recipient_id=?)")
            params.extend([engineer_id, engineer_id])
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at ASC, id ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_direct_messages_for_agent(
        self,
        agent_id: str,
        *,
        limit: int = 100,
        since: float = 0,
        peer_id: str = "",
        peer_kind: str = "user",
        thread_id: str = "",
        include_archived: bool = False,
    ) -> list[dict]:
        """Load recent direct/display messages involving one agent.

        Direct rows include user↔agent conversation plus established system
        display rows. Owner-routed blocking raises (and their replies) remain
        durable routing/audit records but are excluded from this user-DM
        projection. ``peer_id``/``peer_kind`` can narrow the opposite
        participant, while ``thread_id`` keeps future multi-thread callers
        possible without changing storage.
        """
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        where = [
            "(sender_id=? OR recipient_id=?)",
            _AGENT_PEER_MESSAGE_USER_WHERE,
        ]
        params: list = [agent_id, agent_id]
        peer_id = str(peer_id or "").strip()
        peer_kind = str(peer_kind or "").strip()
        if peer_id:
            where.append(
                "((sender_id=? AND recipient_id=?) OR "
                "(sender_id=? AND recipient_id=?))"
            )
            params.extend([agent_id, peer_id, peer_id, agent_id])
        elif peer_kind and peer_kind != "user":
            where.append(
                "((sender_id=? AND recipient_kind=?) OR "
                "(recipient_id=? AND sender_kind=?))"
            )
            params.extend([agent_id, peer_kind, agent_id, peer_kind])
        thread_id = str(thread_id or "").strip()
        if thread_id:
            where.append("thread_id=?")
            params.append(thread_id)
        since_value = _peer_float(since, 0)
        if since_value > 0:
            where.append("created_at>?")
            params.append(since_value)
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_agent_peer_messages_for_thread(
        self,
        thread_id: str,
        *,
        agent_id: str = "",
        limit: int = 1000,
        include_archived: bool = False,
    ) -> list[dict]:
        """Load a peer-message thread oldest first."""
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return []
        limit = max(1, min(int(limit or 1000), 5000))
        where = ["thread_id=?", _AGENT_PEER_MESSAGE_NON_USER_WHERE]
        params: list = [thread_id]
        agent_id = str(agent_id or "").strip()
        if agent_id:
            where.append("(sender_id=? OR recipient_id=?)")
            params.extend([agent_id, agent_id])
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at ASC, id ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_recent_agent_peer_chat_messages(
        self,
        *,
        group_name: str = "",
        limit: int = 5000,
        include_archived: bool = False,
    ) -> list[dict]:
        """Load recent V1 agent↔agent chat rows, newest first.

        V1 chat is the non-user Architect/Engineer peer-message subset:
        Architect↔Engineer, Architect↔Architect, and sanctioned
        Engineer↔Engineer rows. User/direct rows, worker traffic,
        non-message rows, and blocking ask mirrors are intentionally excluded.
        """
        limit = max(1, min(int(limit or 5000), 10000))
        where = [_AGENT_PEER_CHAT_WHERE]
        params: list = []
        group_name = str(group_name or "").strip()
        if group_name:
            where.append("group_name=?")
            params.append(group_name)
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_recent_user_direct_messages(
        self,
        *,
        limit: int = 100,
        include_archived: bool = False,
    ) -> list[dict]:
        """Load recent user↔agent direct rows across all agents, newest first.

        This is the user-conversation lane that feeds remote-channel egress.
        Owner-routed raises are deliberately excluded based on their
        resolver-stamped recipient fields; established system/audit display
        rows remain available to their local surface. Bounded by ``limit``;
        never unbounded.
        """
        limit = max(1, min(int(limit or 100), 1000))
        where = [_AGENT_DIRECT_MESSAGE_WHERE]
        params: list = []
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_agent_peer_chat_messages_for_thread(
        self,
        thread_id: str,
        *,
        limit: int = 5000,
        include_archived: bool = False,
    ) -> list[dict]:
        """Load one V1 agent↔agent chat thread oldest first."""
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return []
        limit = max(1, min(int(limit or 5000), 10000))
        where = ["thread_id=?", _AGENT_PEER_CHAT_WHERE]
        params: list = [thread_id]
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at ASC, id ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_agent_peer_chat_messages_for_pair(
        self,
        first_agent_id: str,
        second_agent_id: str,
        *,
        limit: int = 5000,
        include_archived: bool = False,
    ) -> list[dict]:
        """Load one V1 agent↔agent chat participant-pair oldest first."""
        first_agent_id = str(first_agent_id or "").strip()
        second_agent_id = str(second_agent_id or "").strip()
        if not first_agent_id or not second_agent_id:
            return []
        limit = max(1, min(int(limit or 5000), 10000))
        where = [
            "((sender_id=? AND recipient_id=?) "
            "OR (sender_id=? AND recipient_id=?))",
            _AGENT_PEER_CHAT_WHERE,
        ]
        params: list = [
            first_agent_id,
            second_agent_id,
            second_agent_id,
            first_agent_id,
        ]
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at ASC, id ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_agent_peer_chat_thread(
        self,
        thread_id: str,
        *,
        limit: int = 5000,
        include_archived: bool = False,
    ) -> list[dict]:
        """Compatibility alias for the V1 chat thread loader."""
        return self.load_agent_peer_chat_messages_for_thread(
            thread_id,
            limit=limit,
            include_archived=include_archived,
        )

    def load_direct_messages_for_thread(
        self,
        thread_id: str,
        *,
        agent_id: str = "",
        limit: int = 1000,
        include_archived: bool = False,
    ) -> list[dict]:
        """Load one direct-message thread oldest first."""
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return []
        limit = max(1, min(int(limit or 1000), 5000))
        where = ["thread_id=?", _AGENT_PEER_MESSAGE_USER_WHERE]
        params: list = [thread_id]
        agent_id = str(agent_id or "").strip()
        if agent_id:
            where.append("(sender_id=? OR recipient_id=?)")
            params.extend([agent_id, agent_id])
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at ASC, id ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_buffered_agent_peer_messages(
        self,
        recipient_id: str,
        *,
        limit: int = 100,
    ) -> list[dict]:
        """Load undelivered peer messages for replay, oldest first."""
        recipient_id = str(recipient_id or "").strip()
        if not recipient_id:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages "
            "WHERE recipient_id=? AND delivery_state='buffered' "
            f"AND {_AGENT_PEER_MESSAGE_NON_USER_WHERE} "
            "AND archived_at=0 "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (recipient_id, limit),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_buffered_direct_messages(
        self,
        recipient_id: str,
        *,
        limit: int = 100,
    ) -> list[dict]:
        """Load buffered direct messages for one recipient, oldest first."""
        recipient_id = str(recipient_id or "").strip()
        if not recipient_id:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages "
            "WHERE recipient_id=? AND delivery_state='buffered' "
            f"AND {_BUFFERED_DIRECT_MESSAGE_TRANSPORT_WHERE} "
            "AND archived_at=0 "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (recipient_id, limit),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def load_recent_agent_peer_messages_for_group(
        self,
        group_name: str,
        *,
        limit: int = 100,
        include_archived: bool = False,
    ) -> list[dict]:
        """Load recent peer messages in a group, newest first."""
        group_name = str(group_name or "").strip()
        if not group_name:
            return []
        limit = max(1, min(int(limit or 100), 1000))
        where = ["group_name=?", _AGENT_PEER_MESSAGE_NON_USER_WHERE]
        params: list = [group_name]
        if not include_archived:
            where.append("archived_at=0")
        params.append(limit)
        rows = self._conn.execute(
            "SELECT " + ", ".join(_AGENT_PEER_MESSAGE_COLUMNS) + " "
            "FROM agent_peer_messages WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_decode_agent_peer_message_row(row) for row in rows]

    def update_agent_peer_message_delivery(
        self,
        message_id: str,
        delivery_state: str,
        *,
        reason: str = "",
        delivered_at: float | None = None,
    ) -> dict | None:
        """Persist delivery state for one peer message."""
        message_id = str(message_id or "").strip()
        if not message_id:
            return None
        state = str(delivery_state or "").strip()
        if state not in _AGENT_PEER_MESSAGE_DELIVERY_STATES:
            raise ValueError("delivery_state must be delivered, buffered, or failed")
        if delivered_at is None:
            delivered_value = time.time() if state == "delivered" else 0.0
        else:
            delivered_value = _peer_float(delivered_at, 0)
        self._conn.execute(
            "UPDATE agent_peer_messages SET delivery_state=?, "
            "delivery_reason=?, delivered_at=? WHERE id=?",
            (state, str(reason or ""), delivered_value, message_id),
        )
        self._conn.commit()
        return self.load_agent_peer_message(message_id)

    def mark_peer_message_delivered(
        self,
        message_id: str,
        *,
        delivered: bool = True,
        reason: str = "",
        delivered_at: float | None = None,
    ) -> dict | None:
        """Convenience wrapper for delivered/failed peer-message states."""
        return self.update_agent_peer_message_delivery(
            message_id,
            "delivered" if delivered else "failed",
            reason="" if delivered else reason,
            delivered_at=delivered_at,
        )

    def update_direct_message_delivery(
        self,
        message_id: str,
        delivery_state: str,
        *,
        reason: str = "",
        delivered_at: float | None = None,
    ) -> dict | None:
        """Persist transport delivery state for one direct message."""
        return self.update_agent_peer_message_delivery(
            message_id,
            delivery_state,
            reason=reason,
            delivered_at=delivered_at,
        )

    def mark_direct_message_delivered(
        self,
        message_id: str,
        *,
        delivered: bool = True,
        reason: str = "",
        delivered_at: float | None = None,
    ) -> dict | None:
        """Convenience wrapper for delivered/failed direct-message states."""
        return self.update_direct_message_delivery(
            message_id,
            "delivered" if delivered else "failed",
            reason="" if delivered else reason,
            delivered_at=delivered_at,
        )

    def mark_direct_message_read(
        self,
        message_id: str,
        *,
        read_at: float | None = None,
        reader_id: str = "",
    ) -> dict | None:
        """Persist UI read state for one direct message.

        This intentionally does not alter delivery_state; transport delivery
        and UI read/unread are separate concepts.
        """
        message_id = str(message_id or "").strip()
        if not message_id:
            return None
        reader = str(reader_id or "").strip()
        existing = self.load_agent_peer_message(message_id)
        if not existing:
            return None
        if reader and reader != str(existing.get("recipient_id", "") or ""):
            return existing
        read_value = _peer_float(read_at, 0) if read_at is not None else time.time()
        self._conn.execute(
            "UPDATE agent_peer_messages SET read_at=? WHERE id=?",
            (read_value, message_id),
        )
        self._conn.commit()
        return self.load_agent_peer_message(message_id)

    def save_agent_message_history(self, record: dict) -> dict:
        """Insert a persisted user-message recall entry for one agent."""
        agent_id = str(record.get("agent_id", "") or "").strip()
        message = str(record.get("message", "") or "")
        sent_at = float(record.get("sent_at", time.time()) or time.time())
        cur = self._conn.execute(
            "INSERT INTO agent_message_history "
            "(agent_id, message, sent_at) VALUES (?,?,?)",
            (agent_id, message, sent_at),
        )
        self._conn.commit()
        return {
            "id": cur.lastrowid,
            "agent_id": agent_id,
            "message": message,
            "sent_at": sent_at,
        }

    def load_agent_message_history(self, agent_id: str,
                                   limit: int = 100) -> list[dict]:
        """Load user-message recall entries for an agent, newest first."""
        agent_id = str(agent_id or "").strip()
        limit = max(1, min(int(limit or 100), 1000))
        rows = self._conn.execute(
            "SELECT id, agent_id, message, sent_at "
            "FROM agent_message_history WHERE agent_id=? "
            "ORDER BY sent_at DESC, id DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        cols = ["id", "agent_id", "message", "sent_at"]
        return [dict(zip(cols, row)) for row in rows]

    def load_agent_history(self, status_filter: str = "",
                           limit: int = 50, offset: int = 0
                           ) -> list[dict]:
        """Load agent history records, active first."""
        sql = (
            "SELECT id, name, slug, \"group\", agent_type, template, "
            "created_at, removed_at, worktree_branch, total_tokens_in, "
            "total_tokens_out, total_tasks, status FROM agent_history")
        params: list = []
        if status_filter:
            sql += " WHERE status=?"
            params.append(status_filter)
        sql += (" ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END,"
                " created_at DESC LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["id", "name", "slug", "group", "agent_type", "template",
                "created_at", "removed_at", "worktree_branch",
                "total_tokens_in", "total_tokens_out", "total_tasks",
                "status"]
        return [dict(zip(cols, r)) for r in rows]
    def load_agent_history_detail(self, agent_id: str
                                  ) -> Optional[dict]:
        """Load a single agent history record."""
        row = self._conn.execute(
            "SELECT id, name, slug, \"group\", agent_type, template, "
            "created_at, removed_at, worktree_branch, total_tokens_in, "
            "total_tokens_out, total_tasks, status "
            "FROM agent_history WHERE id=?", (agent_id,)).fetchone()
        if not row:
            return None
        cols = ["id", "name", "slug", "group", "agent_type", "template",
                "created_at", "removed_at", "worktree_branch",
                "total_tokens_in", "total_tokens_out", "total_tasks",
                "status"]
        return dict(zip(cols, row))

    def load_agent_tasks(self, agent_id: str) -> list[dict]:
        """Load task associations for an agent."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, task_title, started_at, "
            "completed_at, outcome FROM agent_tasks "
            "WHERE agent_id=? ORDER BY started_at DESC",
            (agent_id,)).fetchall()
        cols = ["id", "agent_id", "task_id", "task_title",
                "started_at", "completed_at", "outcome"]
        return [dict(zip(cols, r)) for r in rows]

    def load_agent_messages(self, agent_id: str,
                            limit: int = 100) -> list[dict]:
        """Load messages for an agent, newest first."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, timestamp, action, message "
            "FROM agent_messages WHERE agent_id=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (agent_id, limit)).fetchall()
        cols = ["id", "agent_id", "task_id", "timestamp",
                "action", "message"]
        return [dict(zip(cols, r)) for r in rows]

    def load_agent_messages_by_task(self, task_id: str,
                                    limit: int = 100) -> list[dict]:
        """Load messages for a task, newest first."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, timestamp, action, message "
            "FROM agent_messages WHERE task_id=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (task_id, limit)).fetchall()
        cols = ["id", "agent_id", "task_id", "timestamp",
                "action", "message"]
        return [dict(zip(cols, r)) for r in rows]

    def load_all_agent_tasks(self) -> list[dict]:
        """Load all agent-task associations ordered by start time."""
        rows = self._conn.execute(
            "SELECT id, agent_id, task_id, task_title, started_at, "
            "completed_at, outcome FROM agent_tasks "
            "ORDER BY started_at ASC, id ASC").fetchall()
        cols = ["id", "agent_id", "task_id", "task_title",
                "started_at", "completed_at", "outcome"]
        return [dict(zip(cols, r)) for r in rows]

    def load_agent_tasks_window(
        self,
        since: float,
        until: float,
        group: str = "",
    ) -> list[dict]:
        """Load agent-task intervals overlapping a metrics window.

        Rows are left-joined to current board task metadata so the caller can
        scope and label utilization without adding new sampling tables.  A
        missing ``completed_at`` is treated as still running at ``until`` for
        overlap selection; the raw ``completed_at`` value is returned.
        """
        since = float(since or 0.0)
        until = float(until or 0.0)
        clauses = [
            "agent_tasks.started_at <= ?",
            "COALESCE(agent_tasks.completed_at, ?) >= ?",
        ]
        params: list = [until, until, since]
        group = str(group or "").strip()
        if group:
            clauses.append("board_tasks.group_name = ?")
            params.append(group)
        rows = self._conn.execute(
            "SELECT agent_tasks.id, agent_tasks.agent_id, "
            "agent_tasks.task_id, agent_tasks.task_title, "
            "agent_tasks.started_at, agent_tasks.completed_at, "
            "agent_tasks.outcome, "
            "board_tasks.group_name, board_tasks.action_name, "
            "board_tasks.assigned_engineer_id, board_tasks.created_at, "
            "board_tasks.updated_at, board_tasks.lane, "
            "board_tasks.worktree_boundary "
            "FROM agent_tasks "
            "LEFT JOIN board_tasks ON board_tasks.id = agent_tasks.task_id "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY agent_tasks.started_at ASC, agent_tasks.id ASC",
            tuple(params),
        ).fetchall()
        cols = [
            "id",
            "agent_id",
            "task_id",
            "task_title",
            "started_at",
            "completed_at",
            "outcome",
            "group",
            "action_name",
            "assigned_engineer_id",
            "created_at",
            "updated_at",
            "lane",
            "worktree_boundary",
        ]
        out = []
        for row in rows:
            item = dict(zip(cols, row))
            try:
                item["worktree_boundary"] = json.loads(
                    item.get("worktree_boundary", "{}") or "{}"
                )
            except (json.JSONDecodeError, TypeError):
                item["worktree_boundary"] = {}
            out.append(item)
        return out

    def load_mcp_dispatch_calls_window(
        self,
        since: float,
        until: float,
        group: str = "",
    ) -> list[dict]:
        """Load cached Engineer dispatch-tool responses in a time window.

        ``mcp_idempotency`` only stores calls that carried an idempotency key,
        so consumers must treat this as coverage-limited.  The optional
        ``group`` argument is best-effort: when a response exposes a group, it
        is filtered; unscoped responses are returned with ``group=''`` so the
        caller can surface partial coverage instead of fabricating precision.
        """
        since = float(since or 0.0)
        until = float(until or 0.0)
        rows = self._conn.execute(
            "SELECT idempotency_key, surface, tool_name, request_hash, "
            "response_json, created_at, updated_at "
            "FROM mcp_idempotency "
            "WHERE surface = 'engineer' "
            "AND tool_name IN (?, ?) "
            "AND updated_at >= ? AND updated_at <= ? "
            "ORDER BY updated_at ASC, created_at ASC",
            (
                "engineer_task_dispatch",
                "engineer_batch_dispatch",
                since,
                until,
            ),
        ).fetchall()
        requested_group = str(group or "").strip()
        out = []
        for row in rows:
            try:
                raw_response = json.loads(row[4] or "{}")
            except (json.JSONDecodeError, TypeError):
                raw_response = {}
            if not isinstance(raw_response, dict):
                raw_response = {}
            response = _decode_mcp_response_payload(raw_response)
            response_group = _mcp_dispatch_response_group(response)
            if requested_group and response_group \
                    and response_group != requested_group:
                continue
            out.append({
                "idempotency_key": row[0],
                "surface": row[1],
                "tool_name": row[2],
                "request_hash": row[3],
                "response": response,
                "raw_response": raw_response,
                "response_json": row[4],
                "created_at": row[5],
                "updated_at": row[6],
                "group": response_group,
                "unscoped": not bool(response_group),
            })
        return out

    def load_all_agent_history_records(self) -> list[dict]:
        """Load all persisted agent history records."""
        rows = self._conn.execute(
            "SELECT id, name, slug, \"group\", agent_type, template, "
            "created_at, removed_at, worktree_branch, total_tokens_in, "
            "total_tokens_out, total_tasks, status "
            "FROM agent_history ORDER BY created_at ASC").fetchall()
        cols = ["id", "name", "slug", "group", "agent_type", "template",
                "created_at", "removed_at", "worktree_branch",
                "total_tokens_in", "total_tokens_out", "total_tasks",
                "status"]
        return [dict(zip(cols, r)) for r in rows]
