"""Operator Inbox command handlers."""

from __future__ import annotations

from ..dispatch_registry import AsyncHandlerRegistry
from ..state import MatrixState


OPERATOR_NOTICE_COMMAND_NAMES = frozenset({
    "operator_notices_list",
    "operator_notice_mark_read",
    "operator_notices_mark_all_read",
    "operator_notice_resolve",
    "operator_notice_dismiss",
    "operator_notice_archive",
    "operator_notice_restore",
    "operator_notice_report_client_error",
})

_NOTICE_LIFECYCLE_COMMANDS = {
    "operator_notice_mark_read": "read",
    "operator_notice_resolve": "resolve",
    "operator_notice_dismiss": "dismiss",
    "operator_notice_archive": "archive",
    "operator_notice_restore": "restore",
}


async def _handle_operator_notice_command(
    data: dict,
    state: MatrixState,
) -> dict:
    cmd = str(data.get("cmd", "") or "").strip()
    if cmd == "operator_notices_list":
        try:
            limit = max(1, min(500, int(data.get("limit", 200))))
        except (TypeError, ValueError):
            limit = 200
        try:
            offset = max(0, int(data.get("offset", 0)))
        except (TypeError, ValueError):
            offset = 0
        notices = state.list_operator_notices(
            notice_type=data.get("notice_type", ""),
            include_archived=bool(data.get("include_archived", True)),
            limit=limit + 1,
            offset=offset,
        )
        return {
            "type": "operator_notices",
            "notices": notices[:limit],
            "offset": offset,
            "has_more": len(notices) > limit,
            "summary": state.operator_notice_summary(),
        }

    if cmd in _NOTICE_LIFECYCLE_COMMANDS:
        notice_id = str(data.get("id", "") or "").strip()
        if not notice_id:
            return {
                "type": "error",
                "message": "Operator notice id is required",
            }
        try:
            notice = state.update_operator_notice(
                notice_id,
                _NOTICE_LIFECYCLE_COMMANDS[cmd],
                broadcast=False,
            )
        except ValueError as exc:
            return {"type": "error", "message": str(exc)}
        if not notice:
            return {
                "type": "error",
                "message": "Operator notice not found",
            }
        return {
            "type": "operator_notice",
            "notice": notice,
            "summary": state.operator_notice_summary(),
        }

    if cmd == "operator_notices_mark_all_read":
        count = state.mark_all_operator_notices_read(
            notice_type=data.get("notice_type", ""),
            broadcast=False,
        )
        return {
            "type": "operator_notices_marked_read",
            "count": count,
            "summary": state.operator_notice_summary(),
        }

    if cmd == "operator_notice_report_client_error":
        message = str(data.get("message", "") or "").strip()
        if not message:
            return {
                "type": "error",
                "message": "Client error message is required",
            }
        source = str(data.get("source", "") or "").strip()
        notice = state.publish_operator_notice(
            notice_type="alert",
            severity="error",
            category=str(data.get("category", "") or "client"),
            title=str(data.get("title", "") or "Torque error"),
            message=message,
            source=f"client:{source or 'ui'}",
            group_name=data.get("group_name", ""),
            agent_id=data.get("agent_id", ""),
            task_id=data.get("task_id", ""),
            action_kind=data.get("action_kind", ""),
            action_payload=data.get("action_payload", {}),
            dedupe_key=data.get("dedupe_key", ""),
            broadcast=False,
        )
        if not notice:
            return {
                "type": "error",
                "message": "Operator Inbox storage is unavailable",
            }
        return {
            "type": "operator_notice",
            "notice": notice,
            "summary": state.operator_notice_summary(),
        }

    return {
        "type": "error",
        "message": f"Unknown operator notice command: {cmd}",
    }


_OPERATOR_NOTICE_COMMAND_REGISTRY = AsyncHandlerRegistry()
_OPERATOR_NOTICE_COMMAND_REGISTRY.register_many(
    OPERATOR_NOTICE_COMMAND_NAMES,
    _handle_operator_notice_command,
    label="operator_notices",
)
