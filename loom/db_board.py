"""Board task, lane, and auto-dispatch persistence helpers."""

import json
from dataclasses import asdict


_BOARD_TASK_COLUMNS = (
    "id",
    "task",
    "description",
    "slug",
    "group_name",
    "action_name",
    "action_vars",
    "agent_template",
    "instructions",
    "context",
    "criteria",
    "lane",
    "position",
    "agent_id",
    "assigned_engineer_id",
    "created_by_architect_id",
    "suggested_action",
    "reply_agent_id",
    "labels",
    "created_at",
    "updated_at",
    "lane_entered_at",
    "provider",
    "external_id",
    "external_url",
    "parent_task_id",
    "pipeline_depth",
    "pipeline_root_id",
    "status",
    "scheduled_at",
    "messages",
    "depends_on",
    "attachments",
    "health_state",
    "health_since",
    "health_details",
    "artifacts",
    "verification_mode",
    "verification_state",
    "verification_notes",
    "verification_updated_at",
    "verification_updated_by",
    "verification_summary",
    "worktree_boundary",
    "resume_after_boundary_task_id",
    "archived_at",
    "archived_from_lane",
)

_BOARD_TASK_INSERT_SQL = """
    INSERT OR REPLACE INTO board_tasks
        ({columns})
    VALUES ({placeholders})
"""


def _json_loads(value, default):
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default.copy() if isinstance(default, dict) else list(default)


def _serialize_board_task(task):
    d = asdict(task) if not isinstance(task, dict) else dict(task)
    labels = json.dumps(d.pop("labels", []))
    action_vars = json.dumps(d.pop("action_vars", {}))
    messages = json.dumps(d.pop("messages", []))
    depends_on = json.dumps(d.pop("depends_on", []))
    attachments = json.dumps(d.pop("attachments", []))
    health_details = json.dumps(d.pop("health_details", {}))
    artifacts = json.dumps(d.pop("artifacts", []))
    verification_summary = json.dumps(d.pop("verification_summary", {}))
    worktree_boundary = json.dumps(d.pop("worktree_boundary", {}))
    group_name = d.pop("group", d.pop("group_name", ""))
    return (
        d.get("id", ""),
        d.get("task", ""),
        d.get("description", ""),
        d.get("slug", ""),
        group_name,
        d.get("action_name", ""),
        action_vars,
        d.get("agent_template", ""),
        d.get("instructions", ""),
        d.get("context", ""),
        d.get("criteria", ""),
        d.get("lane", "Backlog"),
        d.get("position", 0),
        d.get("agent_id", ""),
        d.get("assigned_engineer_id", ""),
        d.get("created_by_architect_id", ""),
        d.get("suggested_action", ""),
        d.get("reply_agent_id", ""),
        labels,
        d.get("created_at", ""),
        d.get("updated_at", ""),
        d.get("lane_entered_at", ""),
        d.get("provider", ""),
        d.get("external_id", ""),
        d.get("external_url", ""),
        d.get("parent_task_id", ""),
        d.get("pipeline_depth", 0),
        d.get("pipeline_root_id", ""),
        d.get("status", ""),
        d.get("scheduled_at", ""),
        messages,
        depends_on,
        attachments,
        d.get("health_state", "healthy"),
        d.get("health_since", ""),
        health_details,
        artifacts,
        d.get("verification_mode", ""),
        d.get("verification_state", ""),
        d.get("verification_notes", ""),
        d.get("verification_updated_at", ""),
        d.get("verification_updated_by", ""),
        verification_summary,
        worktree_boundary,
        d.get("resume_after_boundary_task_id", ""),
        d.get("archived_at", ""),
        d.get("archived_from_lane", ""),
    )


def insert_board_task(executor, task):
    values = _serialize_board_task(task)
    executor.execute(
        _BOARD_TASK_INSERT_SQL.format(
            columns=", ".join(_BOARD_TASK_COLUMNS),
            placeholders=",".join(["?"] * len(values)),
        ),
        values,
    )


def decode_board_task_row(row, cols):
    d = dict(zip(cols, row))
    d["group"] = d.pop("group_name")
    d["labels"] = _json_loads(d.get("labels", "[]"), [])
    d["action_vars"] = _json_loads(d.get("action_vars", "{}"), {})
    d["messages"] = _json_loads(d.get("messages", "[]"), [])
    d["attachments"] = _json_loads(d.get("attachments", "[]"), [])
    d["artifacts"] = _json_loads(d.get("artifacts", "[]"), [])
    d["depends_on"] = _json_loads(d.get("depends_on", "[]"), [])
    d["health_details"] = _json_loads(d.get("health_details", "{}"), {})
    d["verification_summary"] = _json_loads(
        d.get("verification_summary", "{}"),
        {},
    )
    d["worktree_boundary"] = _json_loads(d.get("worktree_boundary", "{}"), {})
    return d


def replace_board_lanes(executor, lanes):
    executor.execute("DELETE FROM board_lanes")
    for pos, name in enumerate(lanes):
        executor.execute(
            "INSERT INTO board_lanes (name, position) VALUES (?,?)",
            (name, pos),
        )


def replace_auto_dispatch_queue(executor, group_name, entries):
    executor.execute(
        "DELETE FROM auto_dispatch_queue WHERE group_name=?",
        (group_name,),
    )
    for pos, entry in enumerate(entries):
        item = entry if isinstance(entry, dict) else asdict(entry)
        executor.execute(
            "INSERT INTO auto_dispatch_queue "
            "(group_name, position, task_id, agent_group, "
            "max_concurrent, target_agent_id, weaver_owner_id, enqueued_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                group_name,
                pos,
                item.get("task_id", ""),
                item.get("agent_group", ""),
                int(item.get("max_concurrent", 1) or 1),
                item.get("target_agent_id", ""),
                item.get("weaver_owner_id", ""),
                item.get("enqueued_at", ""),
            ),
        )


def decode_auto_dispatch_queue_rows(rows):
    queues = {}
    for row in rows:
        (
            group_name,
            _pos,
            task_id,
            agent_group,
            max_concurrent,
            target_agent_id,
            weaver_owner_id,
            enqueued_at,
        ) = row
        queues.setdefault(group_name, []).append(
            {
                "task_id": task_id,
                "agent_group": agent_group or "",
                "max_concurrent": int(max_concurrent or 1),
                "target_agent_id": target_agent_id or "",
                "weaver_owner_id": weaver_owner_id or "",
                "enqueued_at": enqueued_at or "",
            }
        )
    return queues


class BoardPersistenceMixin:
    """Board-oriented persistence methods mixed into ``LoomDB``."""

    def save_board_task(self, task):
        """Upsert a board task."""
        insert_board_task(self._conn, task)
        self._conn.commit()

    def delete_board_task(self, task_id: str):
        self._conn.execute("DELETE FROM board_tasks WHERE id=?", (task_id,))
        self._conn.commit()

    def save_board_lanes(self, lanes: list):
        """Replace all lanes with new ordered list."""
        replace_board_lanes(self._conn, lanes)
        self._conn.commit()

    def save_auto_dispatch_queue(self, group_name: str, entries: list):
        """Replace one group's auto-dispatch queue."""
        replace_auto_dispatch_queue(self._conn, group_name, entries)
        self._conn.commit()

    def delete_auto_dispatch_queue(self, group_name: str):
        self._conn.execute(
            "DELETE FROM auto_dispatch_queue WHERE group_name=?",
            (group_name,),
        )
        self._conn.commit()
