"""Task-health and board indexing behavior for MatrixState."""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from .config import log
from .state import ARCHIVED_LANE, BoardTask


class BoardHealthMixin:
    def _board_reindex(self, lane: str):
        """Reindex positions for all tasks in a lane."""
        tasks = sorted(
            [t for t in self.board_tasks.values() if t.lane == lane],
            key=lambda t: t.position,
        )
        for i, t in enumerate(tasks):
            t.position = i

    def _task_health_ancestors(self, task_id: str) -> list[str]:
        ancestors = []
        seen = {task_id}
        task = self.board_tasks.get(task_id)
        pid = getattr(task, "parent_task_id", "") if task else ""
        while pid and pid not in seen:
            seen.add(pid)
            parent = self.board_tasks.get(pid)
            if not parent:
                break
            ancestors.append(pid)
            pid = parent.parent_task_id
        return ancestors

    def _task_health_depth(self, task_id: str) -> int:
        depth = 0
        seen = {task_id}
        task = self.board_tasks.get(task_id)
        pid = getattr(task, "parent_task_id", "") if task else ""
        while pid and pid not in seen:
            seen.add(pid)
            parent = self.board_tasks.get(pid)
            if not parent:
                break
            depth += 1
            pid = parent.parent_task_id
        return depth

    def _task_health_context(self, task: BoardTask) -> dict[str, BoardTask]:
        ids = {task.id}
        ids.update(getattr(task, "depends_on", []) or [])
        ids.update(self._tasks_by_parent.get(task.id, set()))
        agent_id = str(getattr(task, "agent_id", "") or "")
        if agent_id:
            ids.update(self._tasks_by_agent.get(agent_id, set()))
        return {
            tid: task
            for tid in ids
            if (task := self.board_tasks.get(tid))
            and task.lane != ARCHIVED_LANE
        }

    def _compute_incremental_task_health(self, task_ids: set[str],
                                         now_ts: float | None):
        from .task_health import (
            ARCHIVED_LANE as HEALTH_ARCHIVED_LANE,
            TaskHealthSnapshot,
            _compute_local_health,
            _roll_up_health,
        )
        if now_ts is None:
            from datetime import datetime, timezone
            now_ts = datetime.now(timezone.utc).timestamp()

        target_ids: set[str] = set()
        for tid in task_ids:
            if tid not in self.board_tasks:
                continue
            target_ids.add(tid)
            target_ids.update(self._task_health_ancestors(tid))

        snapshots = {}
        # Children before parents so aggregate rollups see fresh snapshots
        # for the changed path and stored snapshots for untouched siblings.
        ordered = sorted(
            target_ids,
            key=lambda tid: (self._task_health_depth(tid), tid),
            reverse=True,
        )
        for tid in ordered:
            task = self.board_tasks.get(tid)
            if not task or task.lane == HEALTH_ARCHIVED_LANE:
                continue
            local = _compute_local_health(
                task,
                self._task_health_context(task),
                self.agents,
                now_ts,
            )
            child_snapshots = []
            for child_id in self._tasks_by_parent.get(tid, set()):
                child = self.board_tasks.get(child_id)
                if not child or child.lane in {"Done", HEALTH_ARCHIVED_LANE}:
                    continue
                snapshot = snapshots.get(child_id)
                if snapshot is None:
                    snapshot = TaskHealthSnapshot(
                        state=child.health_state or "healthy",
                        details=dict(child.health_details or {}),
                    )
                child_snapshots.append(snapshot)
            snapshots[tid] = _roll_up_health(
                task,
                local,
                child_snapshots,
                self.board_tasks,
            )
        return snapshots

    def _update_task_health_deadlines(
            self,
            snapshots: dict,
            *,
            now_ts: float,
            replace: bool = False,
    ) -> None:
        from .task_health import next_task_health_deadline

        if replace:
            self._task_health_deadlines = {}
        for tid in list(snapshots.keys()):
            task = self.board_tasks.get(tid)
            if not task or task.lane == ARCHIVED_LANE:
                self._task_health_deadlines.pop(tid, None)
                continue
            deadline = next_task_health_deadline(
                task,
                self.board_tasks,
                self.agents,
                now_ts,
            )
            if deadline:
                self._task_health_deadlines[tid] = float(deadline)
            else:
                self._task_health_deadlines.pop(tid, None)

    def _record_task_health_recompute_metric(
            self,
            *,
            mode: str,
            active_count: int,
            target_count: int,
            changed_count: int,
            duration_ms: float,
    ) -> None:
        meter = getattr(self, "metrics_collector", None)
        if meter is not None and getattr(meter, "enabled", False):
            recorder = getattr(meter, "record_task_health_recompute", None)
            if recorder:
                recorder(
                    mode=mode,
                    active_count=active_count,
                    target_count=target_count,
                    changed_count=changed_count,
                    duration_ms=duration_ms,
                )
        if changed_count or duration_ms >= 50.0:
            log.debug(
                "Task-health recompute mode=%s active=%d target=%d changed=%d duration=%.1fms",
                mode,
                active_count,
                target_count,
                changed_count,
                duration_ms,
            )

    def recompute_task_health(self, now_ts: float | None = None,
                              *, emit: bool = True,
                              persist: bool = True) -> list[str]:
        """Recompute advisory health for dirty tasks and their ancestors.

        Health is deterministic and derived from persisted task signals plus
        live agent state. It never mutates task lanes or statuses. Routine
        broadcast ticks use the dirty set and become a near-no-op when no
        health-affecting deltas have been queued; explicit timestamped calls
        still run a full scan for time-based idle/stalled transitions.
        """
        started = time.perf_counter()
        if not self.board_tasks:
            self._task_health_dirty.clear()
            self._task_health_force_full = False
            self._task_health_deadlines.clear()
            self._task_health_last_recompute_ts = 0.0
            self._record_task_health_recompute_metric(
                mode="empty",
                active_count=0,
                target_count=0,
                changed_count=0,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            return []

        if now_ts is None:
            now_ts = datetime.now(timezone.utc).timestamp()

        force_full = self._task_health_force_full
        if (
                now_ts is not None
                and self._task_health_last_recompute_ts
                and float(now_ts) < self._task_health_last_recompute_ts):
            force_full = True
        dirty_ids = set(self._task_health_dirty)
        due_ids = self._task_health_due_ids(float(now_ts))
        if not force_full and not dirty_ids and not due_ids:
            self._record_task_health_recompute_metric(
                mode="noop",
                active_count=sum(
                    1 for task in self.board_tasks.values()
                    if getattr(task, "lane", "") != ARCHIVED_LANE
                ),
                target_count=0,
                changed_count=0,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            return []

        from .task_health import compute_task_health, now_iso

        if force_full:
            mode = "full"
            snapshots = compute_task_health(self.board_tasks, self.agents,
                                            now_ts=now_ts)
        else:
            target_ids = dirty_ids | due_ids
            if dirty_ids and due_ids:
                mode = "mixed"
            elif due_ids:
                mode = "due"
            else:
                mode = "incremental"
            snapshots = self._compute_incremental_task_health(
                target_ids,
                now_ts,
            )
        self._update_task_health_deadlines(
            snapshots,
            now_ts=float(now_ts),
            replace=force_full,
        )
        changed = []
        changed_set = set()
        changed_task_ids = []
        snapshot_now = now_iso(now_ts)
        for tid, snapshot in snapshots.items():
            task = self.board_tasks.get(tid)
            if not task:
                continue
            old_state = task.health_state or "healthy"
            old_source = ""
            if isinstance(task.health_details, dict):
                old_source = task.health_details.get("source_task_id", "")
            new_source = snapshot.details.get("source_task_id", "")
            state_changed = old_state != snapshot.state
            source_changed = old_source != new_source
            next_since = task.health_since
            if state_changed or source_changed or not next_since:
                next_since = snapshot_now
            if (task.health_state == snapshot.state
                    and task.health_since == next_since
                    and task.health_details == snapshot.details):
                continue
            task.health_state = snapshot.state
            task.health_since = next_since
            task.health_details = snapshot.details
            changed.append(tid)
            changed_set.add(tid)
            changed_task_ids.append(task)

        if not changed:
            self._task_health_dirty.clear()
            self._task_health_force_full = False
            self._task_health_last_recompute_ts = float(now_ts)
            self._record_task_health_recompute_metric(
                mode=mode,
                active_count=sum(
                    1 for task in self.board_tasks.values()
                    if getattr(task, "lane", "") != ARCHIVED_LANE
                ),
                target_count=len(snapshots),
                changed_count=0,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            return []

        # If a task changes, re-emit and persist any open ancestors so root
        # cards can reflect descendant health without waiting for their own
        # direct mutation.
        for task in list(changed_task_ids):
            pid = task.parent_task_id
            while pid:
                parent = self.board_tasks.get(pid)
                if not parent or pid in changed_set:
                    break
                changed_set.add(pid)
                changed.append(pid)
                changed_task_ids.append(parent)
                pid = parent.parent_task_id

        for tid in changed:
            task = self.board_tasks.get(tid)
            if not task:
                continue
            if emit:
                self._suppress_task_health_dirty = True
                try:
                    self.emit_task_upsert(task)
                finally:
                    self._suppress_task_health_dirty = False
            if persist and self.db:
                self._db_save_task(task)
        self._task_health_dirty.clear()
        self._task_health_force_full = False
        self._task_health_last_recompute_ts = float(now_ts)
        self._record_task_health_recompute_metric(
            mode=mode,
            active_count=sum(
                1 for task in self.board_tasks.values()
                if getattr(task, "lane", "") != ARCHIVED_LANE
            ),
            target_count=len(snapshots),
            changed_count=len(changed),
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        return changed

    def _board_next_lane_position(self, lane: str, *, exclude_id: str = "") -> int:
        return max(
            (t.position for t in self.board_tasks.values()
             if t.lane == lane and t.id != exclude_id),
            default=-1,
        ) + 1

    def _board_live_transition_lane(self, group: str, *,
                                    agent_id: str = "") -> str:
        """Return the active lane for a task that has just gone live.

        Backlog represents unscheduled work.  A live task without a concrete
        worker is best represented as To Do; once an agent is attached, prefer
        the group's dispatch lane.
        """
        active_exclusions = {"", "Backlog", "Done", ARCHIVED_LANE}

        def _valid_active(lane: str) -> str:
            lane = str(lane or "").strip()
            if lane and lane in self.board_lanes and lane not in active_exclusions:
                return lane
            return ""

        dispatch_lane = self.get_group_settings(group).dispatch_lane or ""
        if str(agent_id or "").strip():
            candidates = (dispatch_lane, "In Progress", "To Do")
        else:
            candidates = ("To Do", dispatch_lane, "In Progress")
        for lane in candidates:
            active_lane = _valid_active(lane)
            if active_lane:
                return active_lane
        for lane in self.board_lanes:
            active_lane = _valid_active(lane)
            if active_lane:
                return active_lane
        return ""

    def _board_apply_archive_state(self, task: BoardTask, *,
                                   lane: str,
                                   archived_at: str,
                                   archived_from_lane: str,
                                   position: Optional[int] = None,
                                   clear_attention: bool = False,
                                   unlink_agent: bool = False):
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        old_lane = task.lane
        task.lane = lane
        task.archived_at = archived_at
        task.archived_from_lane = archived_from_lane
        if lane == ARCHIVED_LANE:
            task.health_state = "healthy"
            task.health_since = now_iso
        if clear_attention:
            for label in ("torque:blocked", "torque:error"):
                if label in task.labels:
                    task.labels.remove(label)
        if unlink_agent:
            task.agent_id = ""
        if position is not None:
            task.position = position
        else:
            task.position = self._board_next_lane_position(
                lane, exclude_id=task.id
            )
        task.updated_at = now_iso
        if old_lane != lane:
            task.lane_entered_at = now_iso
        self.emit_task_upsert(task)
        self._db_save_task(task)
        # Task watches are event-driven; this central transition path also
        # covers direct moves, archive/unarchive, and done cascades.
        self.evaluate_task_watches_for_task(task.id)
