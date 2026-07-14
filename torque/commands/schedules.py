"""Schedule command handlers and route manifest."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ..dispatch_registry import AsyncHandlerRegistry
from ..state import MatrixState


SCHEDULE_COMMAND_NAMES = frozenset({
    "schedule_create",
    "schedule_update",
    "schedule_remove",
    "schedule_enable",
    "schedule_disable",
    "schedule_list",
    "schedule_run",
})


async def _handle_schedule_command(
        data: dict,
        state: MatrixState,
        *,
        dispatch_command: Callable[[dict], Awaitable],
        panel_event: Callable[..., object],
):
    cmd = str(data.get("cmd", "") or "").strip()
    result = None
    if cmd == "schedule_create":
        name = data.get("name", "").strip()
        group = data.get("group", "")
        if not name:
            result = {"type": "error",
                      "message": "Schedule name is required"}
        elif not group or group not in state.groups:
            result = {"type": "error",
                      "message": "Valid group is required"}
        else:
            cron_expr = data.get("cron_expr", "")
            scheduled_at = data.get("scheduled_at", "")
            tz = data.get("timezone", "")
            if not cron_expr and not scheduled_at:
                result = {"type": "error",
                          "message": "Either cron_expr or "
                                     "scheduled_at is required"}
            else:
                if cron_expr:
                    try:
                        from ..cron import parse_cron, \
                            next_run as cron_next
                        parse_cron(cron_expr)
                        nxt = cron_next(
                            cron_expr,
                            datetime.now(timezone.utc), tz=tz)
                        next_run_at = nxt.isoformat()
                    except ValueError as e:
                        result = {
                            "type": "error",
                            "message": f"Invalid cron: {e}"}
                        next_run_at = None
                else:
                    next_run_at = scheduled_at

                if next_run_at is not None:
                    kwargs = {
                        "task_template":
                            data.get("task_template", ""),
                        "description":
                            data.get("description", ""),
                        "action_name":
                            data.get("action_name", ""),
                        "action_vars":
                            data.get("action_vars", {}),
                        "agent_template":
                            data.get("agent_template", ""),
                        "labels": data.get("labels", []),
                        "cron_expr": cron_expr,
                        "scheduled_at": scheduled_at,
                        "timezone": tz,
                        "next_run_at": next_run_at,
                        "enabled":
                            data.get("enabled", True),
                    }
                    sched = state.schedule_add(
                        name, group, **kwargs)
                    if sched:
                        result = {"type": "ok",
                                  "schedule_id": sched.id}
                    else:
                        result = {"type": "error",
                                  "message":
                                      "Failed to create "
                                      "schedule"}

    elif cmd == "schedule_update":
        sid = data.get("id", "")
        sched = state.schedules.get(sid)
        if not sched:
            result = {"type": "error",
                      "message": "Schedule not found"}
        else:
            fields = {}
            for k in ("name", "task_template", "description",
                      "group", "action_name", "action_vars",
                      "agent_template", "labels", "cron_expr",
                      "scheduled_at", "timezone", "enabled"):
                if k in data:
                    fields[k] = data[k]
            new_cron = fields.get("cron_expr", sched.cron_expr)
            new_at = fields.get("scheduled_at",
                                sched.scheduled_at)
            new_tz = fields.get("timezone", sched.timezone)
            if "cron_expr" in fields or "scheduled_at" in fields \
                    or "timezone" in fields:
                if new_cron:
                    try:
                        from ..cron import parse_cron, \
                            next_run as cron_next
                        parse_cron(new_cron)
                        nxt = cron_next(
                            new_cron,
                            datetime.now(timezone.utc),
                            tz=new_tz)
                        fields["next_run_at"] = nxt.isoformat()
                    except ValueError as e:
                        result = {
                            "type": "error",
                            "message":
                                f"Invalid cron: {e}"}
                        fields = None
                elif new_at:
                    fields["next_run_at"] = new_at
            if fields is not None:
                state.schedule_update(sid, **fields)

    elif cmd == "schedule_remove":
        sid = data.get("id", "")
        if sid in state.schedules:
            state.schedule_remove(sid)
        else:
            result = {"type": "error",
                      "message": "Schedule not found"}

    elif cmd == "schedule_enable":
        sid = data.get("id", "")
        sched = state.schedules.get(sid)
        if not sched:
            result = {"type": "error",
                      "message": "Schedule not found"}
        else:
            fields = {"enabled": True}
            if sched.cron_expr:
                from ..cron import next_run as cron_next
                nxt = cron_next(sched.cron_expr,
                                datetime.now(timezone.utc),
                                tz=sched.timezone)
                fields["next_run_at"] = nxt.isoformat()
            elif sched.scheduled_at:
                fields["next_run_at"] = sched.scheduled_at
            state.schedule_update(sid, **fields)

    elif cmd == "schedule_disable":
        sid = data.get("id", "")
        if sid in state.schedules:
            state.schedule_update(sid, enabled=False)
        else:
            result = {"type": "error",
                      "message": "Schedule not found"}

    elif cmd == "schedule_list":
        result = {
            "type": "schedule_list",
            "schedules": [
                asdict(s) for s in state.schedules.values()
            ],
        }

    elif cmd == "schedule_run":
        sid = data.get("id", "")
        sched = state.schedules.get(sid)
        if not sched:
            result = {"type": "error",
                      "message": "Schedule not found"}
        elif sched.group not in state.groups:
            result = {"type": "error",
                      "message": "Schedule group not found"}
        else:
            now = datetime.now(timezone.utc)
            title = sched.task_template or sched.name
            title = (title
                     .replace("{date}",
                              now.strftime("%Y-%m-%d"))
                     .replace("{time}",
                              now.strftime("%H:%M"))
                     .replace("{datetime}",
                              now.strftime("%Y-%m-%d %H:%M")))
            task = state.board_add_task(
                task=title, group=sched.group,
                lane="Backlog",
                description=sched.description,
                action_name=sched.action_name,
                action_vars=dict(sched.action_vars),
                agent_template=sched.agent_template,
                labels=list(sched.labels),
                board_sync={
                    "version": 1,
                    "auto_track": False,
                    "auto_sync_excluded": True,
                    "auto_sync_excluded_reason": "schedule",
                })
            if task:
                await handle_command({
                    "cmd": "dispatch_task",
                    "id": task.id,
                    "create_agent": True})
                sched.last_run_at = now.isoformat()
                sched.run_count += 1
                sched.last_task_id = task.id
                state._emit("schedule_upsert",
                            **asdict(sched))
                state._db_save_schedule(sched)
                _panel_event("schedule_fired", "",
                             sched.name, sched.group,
                             title, task_id=task.id)
                result = {"type": "ok",
                          "task_id": task.id}

    return result


_SCHEDULE_COMMAND_REGISTRY = AsyncHandlerRegistry()
_SCHEDULE_COMMAND_REGISTRY.register_many(
    SCHEDULE_COMMAND_NAMES,
    _handle_schedule_command,
    label="schedules",
)
