"""Queueing, scheduling, and shared-worktree dispatch helpers."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone

from .config import log
from .state import ARCHIVED_LANE, MatrixState, task_is_closed
from .worktree_streams import (
    compute_worktree_stream,
    compute_worktree_stream_for_task,
)


def _should_queue_existing_agent_dispatch(active_task, *,
                                          target_task_id: str,
                                          self_dispatch: bool) -> bool:
    """Return whether an existing-agent dispatch should be queued."""
    if self_dispatch:
        return False
    if not active_task:
        return False
    if active_task.id == target_task_id:
        return False
    return not task_is_closed(active_task)


def _cells_share_worktree_context(a, b) -> bool:
    """Return whether two agents point at the same branch/worktree context."""
    if not a or not b or a.id == b.id:
        return False
    if a.worktree_path and b.worktree_path and a.worktree_path == b.worktree_path:
        return True
    if (
        a.worktree_branch and b.worktree_branch
        and a.worktree_branch == b.worktree_branch
        and a.worktree_repo_root and b.worktree_repo_root
        and a.worktree_repo_root == b.worktree_repo_root
    ):
        return True
    return False


def _is_busy_agent(state: MatrixState, agent_id: str) -> bool:
    cell = state.agents.get(agent_id)
    if not cell or cell.cell_type != "agent":
        return False
    return state.agent_is_busy(agent_id)


def _active_worker_ids(state: MatrixState, group: str) -> set[str]:
    active = set()
    engineer_id = state.get_group_settings(group).engineer_agent_id
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if cell.group != group:
            continue
        if cell.id == engineer_id:
            continue
        if _is_busy_agent(state, cell.id):
            active.add(cell.id)
    return active


def _agent_has_active_worktree_context(state: MatrixState, cell) -> bool:
    """Return whether an agent is actively working on a worktree task."""
    return bool(
        cell
        and cell.cell_type == "agent"
        and state.agent_is_busy(cell.id)
        and (cell.worktree_path or cell.worktree_branch)
    )


def _find_active_worktree_owner(state: MatrixState, cell):
    """Return the active agent currently owning a shared worktree context."""
    if not cell or not (cell.worktree_path or cell.worktree_branch):
        return None
    for other in state.agents.values():
        if not _agent_has_active_worktree_context(state, other):
            continue
        if _cells_share_worktree_context(cell, other):
            return other
    return None


def _stream_head_queue_item(stream: dict | None) -> dict:
    if not stream:
        return {}
    queue_items = stream.get("queue_items", []) or []
    return dict(queue_items[0]) if queue_items else {}


def _stream_ready_to_resume_task_id(stream: dict | None) -> str:
    if not stream:
        return ""
    head = _stream_head_queue_item(stream)
    if head.get("queue_state", "") == "ready_to_resume" and head.get("deps_met", False):
        return str(head.get("task_id", "") or "").strip()
    task_id = str(stream.get("ready_to_resume_task_id", "") or "").strip()
    return task_id


def _stream_became_ready_to_resume(previous_stream: dict | None,
                                   current_stream: dict | None) -> bool:
    current_head = _stream_head_queue_item(current_stream)
    if current_head.get("queue_state", "") != "ready_to_resume":
        return False
    previous_head = _stream_head_queue_item(previous_stream)
    if not previous_head:
        return bool(previous_stream is not None)
    return previous_head.get("queue_state", "") != "ready_to_resume"


def _stream_resume_target_agent_id(state: MatrixState, stream: dict,
                                   task) -> str:
    candidate_ids = []
    for agent_id in (
        str(stream.get("agent_id", "") or "").strip(),
        str(getattr(task, "agent_id", "") or "").strip(),
    ):
        if agent_id and agent_id not in candidate_ids:
            candidate_ids.append(agent_id)

    for agent_id in candidate_ids:
        cell = state.agents.get(agent_id)
        if not cell or cell.cell_type != "agent":
            continue
        if cell.group != getattr(task, "group", ""):
            continue
        current_task = state.agent_current_task(agent_id)
        if current_task and getattr(current_task, "id", "") != getattr(task, "id", ""):
            continue
        return agent_id
    return ""


def _stream_resume_allowed(settings, *,
                           previous_stream: dict | None,
                           current_stream: dict | None) -> bool:
    autonomy_mode = str(getattr(settings, "autonomy_mode", "") or "").strip()
    if autonomy_mode == "suggest_only":
        return False
    if autonomy_mode == "aggressive_auto_continue":
        return True
    return _stream_became_ready_to_resume(previous_stream, current_stream)


async def _maybe_auto_resume_stream(state: MatrixState, handle_command,
                                    panel_event, *,
                                    task=None,
                                    previous_stream: dict | None = None,
                                    group: str = "") -> dict | None:
    """Auto-resume the next ready product task for a computed stream."""
    if not task and not previous_stream:
        return None

    current_stream = None
    if task:
        current_stream = compute_worktree_stream_for_task(
            state,
            getattr(task, "id", "") or "",
            group=group or getattr(task, "group", "") or "",
        )
    if not current_stream and previous_stream:
        repo_root = str(previous_stream.get("repo_root", "") or "").strip()
        branch = str(previous_stream.get("branch", "") or "").strip()
        if repo_root and branch:
            current_stream = compute_worktree_stream(
                state,
                repo_root=repo_root,
                branch=branch,
                group=group or previous_stream.get("group", "") or "",
            )

    if not current_stream:
        return None

    ready_task_id = _stream_ready_to_resume_task_id(current_stream)
    if not ready_task_id:
        return None
    ready_task = state.board_tasks.get(ready_task_id)
    if not ready_task or task_is_closed(ready_task):
        return None
    if ready_task.lane not in {"Backlog", "To Do"}:
        return None
    if not state.board_deps_met(ready_task):
        return None

    stream_group = (
        str(current_stream.get("group", "") or "").strip()
        or getattr(ready_task, "group", "")
        or group
    )
    if not stream_group:
        return None
    settings = state.get_engineer_settings(stream_group)
    if not _stream_resume_allowed(
            settings,
            previous_stream=previous_stream,
            current_stream=current_stream):
        return None

    target_agent_id = _stream_resume_target_agent_id(
        state,
        current_stream,
        ready_task,
    )
    if not target_agent_id:
        return None

    result = await handle_command({
        "cmd": "dispatch_task",
        "id": ready_task.id,
        "agent_id": target_agent_id,
    })
    if result and result.get("type") == "error":
        return result

    agent = state.agents.get(target_agent_id)
    panel_event(
        "task_auto_dispatched",
        target_agent_id,
        agent.name if agent else "",
        stream_group,
        ready_task.task[:80],
        task_id=ready_task.id,
    )
    return {
        "type": "stream_auto_resumed",
        "stream_id": current_stream.get("stream_id", ""),
        "task_id": ready_task.id,
        "agent_id": target_agent_id,
    }


def _capture_auto_resume_targets(state: MatrixState, *, task=None,
                                 group: str = "") -> list[dict]:
    """Snapshot affected streams before a mutation that may clear gates/deps."""
    targets = []
    seen_stream_ids: set[str] = set()

    def _append_target(candidate):
        if not candidate or task_is_closed(candidate):
            return
        candidate_group = str(getattr(candidate, "group", "") or group).strip()
        stream = compute_worktree_stream_for_task(
            state,
            getattr(candidate, "id", "") or "",
            group=candidate_group,
        )
        if not stream:
            return
        stream_id = str(stream.get("stream_id", "") or "").strip()
        if not stream_id or stream_id in seen_stream_ids:
            return
        seen_stream_ids.add(stream_id)
        targets.append({
            "task_id": getattr(candidate, "id", "") or "",
            "group": candidate_group,
            "previous_stream": stream,
        })

    _append_target(task)
    if task:
        for dependent in state.board_get_dependents(getattr(task, "id", "") or ""):
            _append_target(dependent)
    return targets


async def _maybe_auto_resume_targets(state: MatrixState, handle_command,
                                     panel_event, *, targets: list[dict] | None = None,
                                     group: str = "") -> list[dict]:
    """Try stream auto-resume across a set of pre-snapshotted stream members."""
    results = []
    for item in targets or []:
        task_id = str((item or {}).get("task_id", "") or "").strip()
        if not task_id:
            continue
        task = state.board_tasks.get(task_id)
        result = await _maybe_auto_resume_stream(
            state,
            handle_command,
            panel_event,
            task=task,
            previous_stream=(item or {}).get("previous_stream"),
            group=str((item or {}).get("group", "") or group).strip(),
        )
        if result:
            results.append(result)
    return results


async def _pump_auto_dispatch_queue(state: MatrixState, handle_command,
                                    panel_event, *, group: str = "") -> list:
    """Dispatch queued tasks when concurrency allows."""
    dispatched = []
    groups = [group] if group else list(state.auto_dispatch_queues)
    for group_name in groups:
        while True:
            queue = state.auto_dispatch_queues.get(group_name, [])
            if not queue:
                break
            entry = queue[0]
            task = state.board_tasks.get(entry.task_id)
            if (
                not task
                or task.group != group_name
                or task_is_closed(task)
                or (
                    task.agent_id
                    and task.agent_id != entry.target_agent_id
                )
            ):
                state.auto_dispatch_queue_remove_task(entry.task_id)
                continue
            if not state.board_deps_met(task):
                break
            if task.scheduled_at:
                now_iso = datetime.now(timezone.utc).isoformat()
                if task.scheduled_at > now_iso:
                    break
            target_agent_id = entry.target_agent_id
            if target_agent_id:
                target = state.agents.get(target_agent_id)
                if not target or target.cell_type != "agent" or target.group != group_name:
                    entry.target_agent_id = ""
                    state._db_save_auto_dispatch_queue(group_name)
                    target_agent_id = ""
            active_agents = _active_worker_ids(state, group_name)
            needs_capacity = not target_agent_id
            if target_agent_id:
                needs_capacity = not _is_busy_agent(state, target_agent_id)
            if needs_capacity and len(active_agents) >= entry.max_concurrent:
                break

            payload = {"cmd": "dispatch_task", "id": task.id}
            if entry.engineer_owner_id:
                payload["_engineer_dispatch_group"] = group_name
                payload["_engineer_dispatch_id"] = entry.engineer_owner_id
            if target_agent_id:
                payload["agent_id"] = target_agent_id
            else:
                payload["create_agent"] = True
                if entry.provider:
                    payload["agent_type"] = entry.provider
                if entry.engineer_owner_id:
                    payload["_created_by_engineer_id"] = entry.engineer_owner_id
            result = await handle_command(payload)
            if result and result.get("type") in {"error", "dispatch_action_missing"}:
                state.auto_dispatch_queue_remove_task(task.id)
                continue

            task_after = state.board_tasks.get(task.id)
            resolved_agent_id = task_after.agent_id if task_after else ""
            if entry.agent_group and resolved_agent_id:
                state.auto_dispatch_queue_bind_agent_group(
                    group_name,
                    entry.agent_group,
                    resolved_agent_id,
                )
            state.auto_dispatch_queue_remove_task(task.id)
            if resolved_agent_id:
                agent = state.agents.get(resolved_agent_id)
                panel_event(
                    "task_auto_dispatched",
                    resolved_agent_id,
                    agent.name if agent else "",
                    group_name,
                    task.task[:80],
                    task_id=task.id,
                )
            dispatched.append({
                "group": group_name,
                "task_id": task.id,
                "agent_id": resolved_agent_id,
            })
    return dispatched


async def _pump_auto_dispatch_queue_forever(state: MatrixState,
                                            handle_command,
                                            panel_event,
                                            *,
                                            interval: float = 10.0) -> None:
    """Run the auto-dispatch pump on a persistent interval.

    Survives per-cycle exceptions so a single failing group cannot halt the
    whole pump. Completion handlers still trigger an immediate drain via
    ``_pump_auto_dispatch_queue`` for responsiveness.
    """
    while True:
        try:
            await _pump_auto_dispatch_queue(
                state, handle_command, panel_event
            )
        except Exception:
            log.exception("Auto-dispatch pump cycle failed")
        await asyncio.sleep(interval)


def _should_handoff_shared_worktree(owner, *,
                                    target_agent_id: str,
                                    handoff_from: str) -> bool:
    """Return whether an explicit handoff should transfer branch ownership."""
    return bool(
        owner
        and handoff_from
        and owner.id == handoff_from
        and owner.id != target_agent_id
    )


async def _scheduler_loop(state: MatrixState, handle_command, panel_event):
    """Periodically check for due schedules and scheduled tasks."""
    from datetime import datetime, timezone as dt_tz
    from .cron import next_run as cron_next_run

    while True:
        await asyncio.sleep(30)
        now = datetime.now(dt_tz.utc)
        now_iso = now.isoformat()

        task_changed = False
        for task in list(state.board_tasks.values()):
            if not task.scheduled_at or task.scheduled_at > now_iso:
                continue
            if task.agent_id:
                continue
            if task.lane in ("In Progress", "Done", ARCHIVED_LANE):
                continue
            log.info("Scheduled task '%s' (%s) is due — dispatching",
                     task.task, task.id)
            task.scheduled_at = ""
            state._emit("task_upsert", **asdict(task))
            state._db_save_task(task)
            task_changed = True
            try:
                await handle_command({
                    "cmd": "dispatch_task",
                    "id": task.id,
                    "create_agent": True,
                })
                panel_event(
                    "task_scheduled_dispatch", "", "", task.group,
                    task.task[:80], task_id=task.id,
                )
            except Exception:
                log.exception("Failed to dispatch scheduled task '%s'", task.id)
        if task_changed:
            await state.broadcast()

        due = state.schedule_get_due(now_iso)
        if not due:
            continue

        for sched in due:
            try:
                title = sched.task_template or sched.name
                title = (
                    title
                    .replace("{date}", now.strftime("%Y-%m-%d"))
                    .replace("{time}", now.strftime("%H:%M"))
                    .replace("{datetime}", now.strftime("%Y-%m-%d %H:%M"))
                )

                if sched.group not in state.groups:
                    log.warning(
                        "Schedule '%s': group '%s' no longer exists — skipping",
                        sched.name,
                        sched.group,
                    )
                    continue

                task = state.board_add_task(
                    task=title,
                    group=sched.group,
                    lane="Backlog",
                    description=sched.description,
                    action_name=sched.action_name,
                    action_vars=dict(sched.action_vars),
                    agent_template=sched.agent_template,
                    labels=list(sched.labels),
                )
                if not task:
                    log.warning("Schedule '%s': failed to create task", sched.name)
                    continue

                log.info("Schedule '%s' fired — created task '%s' (%s)",
                         sched.name, task.task, task.id)

                await handle_command({
                    "cmd": "dispatch_task",
                    "id": task.id,
                    "create_agent": True,
                })

                sched.last_run_at = now_iso
                sched.run_count += 1
                sched.last_task_id = task.id

                if sched.cron_expr:
                    try:
                        nxt = cron_next_run(sched.cron_expr, now, tz=sched.timezone)
                        sched.next_run_at = nxt.isoformat()
                    except ValueError:
                        log.error(
                            "Schedule '%s': invalid cron '%s' — disabling",
                            sched.name,
                            sched.cron_expr,
                        )
                        sched.enabled = False
                        sched.next_run_at = ""
                else:
                    sched.enabled = False
                    sched.next_run_at = ""

                state._emit("schedule_upsert", **asdict(sched))
                state._db_save_schedule(sched)

                panel_event("schedule_fired", "", sched.name,
                            sched.group, title, task_id=task.id)

            except Exception:
                log.exception("Schedule '%s' failed to fire", sched.name)

        await state.broadcast()
