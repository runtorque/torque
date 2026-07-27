"""Board task and lane mutation behavior for MatrixState."""

from __future__ import annotations

import copy
import time
from dataclasses import asdict, fields
from datetime import datetime, timezone
from typing import Optional

from .artifacts import normalize_artifacts, normalize_attachments
from .task_ids import format_root_task_id, is_canonical_task_id, parse_task_id
from .finalization import audit_entry, evaluate_finalization, normalize_mode, status_projection
from .state import (
    ARCHIVED_LANE,
    _ENGINEER_MESSAGE_EXPIRY_NOTE,
    _RESERVED_LANES,
    TASK_DISPATCH_STATE_LIVE,
    TASK_DISPATCH_STATE_QUEUED,
    BoardTask,
    CriticalWriteCapture,
    _append_unique_string,
    _coverage_card_message,
    _normalize_board_sync,
    _normalize_completion_evidence,
    _normalize_messages_thread,
    _normalize_task_dispatch_state,
    _normalize_verification_fields,
    board_task_is_closed,
    task_counts_as_done,
    task_is_engineer_message_followup,
    task_suppresses_done_cascade,
)


class BoardMutationMixin:
    def board_add_task(self, task: str, group: str, lane: str = "",
                       **kwargs) -> Optional[BoardTask]:
        if not task:
            return None
        if not group or group not in self.groups:
            return None
        if not lane:
            lane = self.board_lanes[0] if self.board_lanes else "Backlog"
        if lane not in self.board_lanes:
            return None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        explicit_id = kwargs.pop("id", None)
        parent_task_id = self.resolve_task_alias(
            kwargs.get("parent_task_id", "") or ""
        )
        pipeline_root_id = self.resolve_task_alias(
            kwargs.get("pipeline_root_id", "") or ""
        )
        if parent_task_id:
            kwargs["parent_task_id"] = parent_task_id
        if pipeline_root_id:
            kwargs["pipeline_root_id"] = pipeline_root_id
        alias_id = ""
        if explicit_id:
            resolved = self._alias_or_use_task_id(explicit_id)
            if not resolved:
                return None
            tid, alias_id = resolved
        elif parent_task_id or pipeline_root_id:
            root_id = pipeline_root_id or parent_task_id
            try:
                candidate_id = self._allocate_derived_task_id(group, root_id)
                resolved = self._alias_or_use_task_id(candidate_id)
                if not resolved:
                    return None
                tid, alias_id = resolved
            except ValueError:
                tid = self._new_ephemeral_task_id()
        else:
            while True:
                candidate_id = self._allocate_root_task_id(group)
                resolved = self._alias_or_use_task_id(candidate_id)
                if resolved:
                    tid, alias_id = resolved
                    break
        task_slug = self._unique_task_slug(task)
        # Validate depends_on: strip non-existent IDs
        if "depends_on" in kwargs:
            deps = kwargs["depends_on"]
            if isinstance(deps, list):
                normalized = []
                for dep_id in deps:
                    resolved_dep = self.resolve_task_alias(dep_id)
                    if resolved_dep in self.board_tasks:
                        normalized.append(resolved_dep)
                kwargs["depends_on"] = normalized
            else:
                kwargs.pop("depends_on", None)
        if "attachments" in kwargs:
            kwargs["attachments"] = normalize_attachments(
                kwargs["attachments"])
        if "artifacts" in kwargs:
            kwargs["artifacts"] = normalize_artifacts(kwargs["artifacts"])
        if "messages_thread" in kwargs:
            kwargs["messages_thread"] = _normalize_messages_thread(
                kwargs["messages_thread"]
            )
        if "dispatch_state" in kwargs:
            kwargs["dispatch_state"] = _normalize_task_dispatch_state(
                kwargs.get("dispatch_state")
            )
        elif str(kwargs.get("agent_id", "") or "").strip():
            kwargs["dispatch_state"] = TASK_DISPATCH_STATE_LIVE
        else:
            kwargs["dispatch_state"] = TASK_DISPATCH_STATE_QUEUED
        if (
                kwargs["dispatch_state"] == TASK_DISPATCH_STATE_LIVE
                and lane == "Backlog"):
            live_lane = self._board_live_transition_lane(
                group,
                agent_id=kwargs.get("agent_id", ""),
            )
            if live_lane:
                lane = live_lane
        if "board_sync" in kwargs:
            kwargs["board_sync"] = _normalize_board_sync(kwargs["board_sync"])
        _normalize_verification_fields(kwargs)
        bt = BoardTask(
            id=tid,
            task=task,
            slug=task_slug,
            group=group,
            lane=lane,
            position=self._board_next_lane_position(lane),
            created_at=now,
            updated_at=now,
            lane_entered_at=now,
            **{k: v for k, v in kwargs.items()
               if k in BoardTask.__dataclass_fields__ and k not in
               ("id", "task", "slug", "group", "lane", "position",
                "created_at", "updated_at", "lane_entered_at")},
        )
        self.board_tasks[tid] = bt
        if alias_id and alias_id != tid:
            self.task_id_aliases[alias_id] = tid
            self._db_save_task_id_alias(alias_id)
        if is_canonical_task_id(tid):
            parsed = parse_task_id(tid)
            if parsed:
                prefix = parsed["prefix"]
                self.task_id_counters[prefix] = max(
                    self.task_id_counters.get(prefix, 1),
                    parsed["root_number"] + 1,
                )
                self._db_save_task_id_counter(prefix)
                if parsed["child_number"] is not None:
                    root_id = kwargs.get("pipeline_root_id", "") or format_root_task_id(
                        prefix, parsed["root_number"]
                    )
                    self.pipeline_task_counters[root_id] = max(
                        self.pipeline_task_counters.get(root_id, 1),
                        parsed["child_number"] + 1,
                    )
                    self._db_save_pipeline_task_counter(root_id)
        self._emit("task_upsert", **asdict(bt))
        self._db_save_task(bt)
        self.recompute_task_health()
        return bt

    def board_mark_task_covered(
            self,
            tid: str,
            *,
            covering_task_id: str = "",
            pr_url: str = "",
            sha: str = "",
            tests_run: str = "",
            evidence: str = "",
            notes: str = "",
            actor_name: str = "Torque",
            actor_id: str = "",
            actor_kind: str = "",
            authorization: dict | None = None,
            move_to_done: bool = False) -> dict:
        """Record durable evidence that one card is covered elsewhere.

        This intentionally does not infer coverage.  Callers provide the
        covering task/PR/SHA/test evidence, and this method appends an
        auditable task-history message plus a structured completion_evidence
        entry.  ``move_to_done`` uses normal board move semantics so cascades
        and health updates stay centralized.
        """
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task:
            raise ValueError("Task not found")

        def _clean(value) -> str:
            return str(value or "").strip()

        covering_task_id = self.resolve_task_alias(_clean(covering_task_id))
        pr_url = _clean(pr_url)
        sha = _clean(sha)
        tests_run = _clean(tests_run)
        evidence = _clean(evidence)
        notes = _clean(notes)
        actor_name = _clean(actor_name) or "Torque"
        actor_id = _clean(actor_id)
        actor_kind = _clean(actor_kind)

        covering_task = None
        if covering_task_id:
            if covering_task_id == tid:
                raise ValueError("covering_task_id must reference another task")
            covering_task = self.board_tasks.get(covering_task_id)
            if not covering_task:
                raise ValueError("Covering task not found")
        if not any((covering_task_id, pr_url, sha, tests_run, evidence, notes)):
            raise ValueError(
                "At least one coverage evidence field is required"
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        covered_by = {
            "recorded_at": now_iso,
            "recorded_by": actor_name,
            "recorded_by_id": actor_id,
            "recorded_by_kind": actor_kind,
            "moved_to_done": bool(move_to_done),
        }
        if covering_task_id:
            covered_by["task_id"] = covering_task_id
            if covering_task:
                covered_by["task_title"] = str(
                    getattr(covering_task, "task", "") or ""
                ).strip()
        for key, value in (
                ("pr_url", pr_url),
                ("sha", sha),
                ("tests_run", tests_run),
                ("evidence", evidence),
                ("notes", notes),
        ):
            if value:
                covered_by[key] = value
        if isinstance(authorization, dict) and authorization:
            covered_by["authorization"] = dict(authorization)

        completion_evidence = _normalize_completion_evidence(
            getattr(task, "completion_evidence", {}) or {}
        )
        completion_evidence["status"] = "evidence_attached"
        completion_evidence["sources"] = _append_unique_string(
            completion_evidence.get("sources", []),
            "covered_by",
        )
        completion_evidence["covered_by"] = covered_by
        completion_evidence["updated_by"] = actor_name
        completion_evidence["updated_at"] = now_iso

        messages = list(getattr(task, "messages", []) or [])
        message = _coverage_card_message(covered_by)
        messages.append({
            "timestamp": time.time(),
            "action": "covered_by",
            "message": message,
            "agent_name": actor_name,
            "agent_id": actor_id,
            "agent_kind": actor_kind,
        })

        update_fields = {
            "completion_evidence": completion_evidence,
            "messages": messages,
        }
        if move_to_done:
            update_fields["status"] = ""
        self.board_update_task(tid, **update_fields)
        if move_to_done:
            self.board_move_task(tid, "Done", clear_status=True)

        refreshed = self.board_tasks.get(tid)
        return {
            "type": "task_marked_covered",
            "task_id": tid,
            "lane": str(getattr(refreshed, "lane", "") or ""),
            "covered_by": covered_by,
            "message": message,
            "moved_to_done": bool(move_to_done),
        }

    def board_finalize_existing_task_coverage(
            self,
            tid: str,
            *,
            actor_name: str = "Torque",
            actor_id: str = "",
            actor_kind: str = "",
            reason: str = "") -> dict:
        """Move an already-covered task to Done without rewriting evidence.

        ``board_mark_task_covered(..., move_to_done=True)`` is the normal path
        when the caller is attaching coverage and closing the card in one
        operation.  Backlog hygiene for older cards needs a narrower path: keep
        the existing ``completion_evidence.covered_by`` record (including the
        original actor, route authorization, PR/SHA/test notes, and timestamp),
        append finalization metadata, add an auditable history message, then use
        normal board move semantics.
        """
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task:
            raise ValueError("Task not found")
        if "Done" not in self.board_lanes:
            raise ValueError("Done lane not available")

        completion_evidence = _normalize_completion_evidence(
            getattr(task, "completion_evidence", {}) or {}
        )
        covered_by = completion_evidence.get("covered_by", {}) or {}
        if not isinstance(covered_by, dict) or not covered_by:
            raise ValueError("Task has no existing covered_by evidence")

        actor_name = str(actor_name or "").strip() or "Torque"
        actor_id = str(actor_id or "").strip()
        actor_kind = str(actor_kind or "").strip()
        reason = str(reason or "").strip() or (
            "Finalized already-covered task using preserved coverage evidence."
        )
        now_iso = datetime.now(timezone.utc).isoformat()

        finalized_by = copy.deepcopy(covered_by)
        finalized_by["moved_to_done"] = True
        finalized_by["finalized_at"] = now_iso
        finalized_by["finalized_by"] = actor_name
        finalized_by["finalized_by_id"] = actor_id
        finalized_by["finalized_by_kind"] = actor_kind
        finalized_by["finalized_reason"] = reason

        completion_evidence["status"] = (
            str(completion_evidence.get("status", "") or "").strip()
            or "evidence_attached"
        )
        completion_evidence["sources"] = _append_unique_string(
            completion_evidence.get("sources", []),
            "covered_by",
        )
        completion_evidence["covered_by"] = finalized_by
        completion_evidence["updated_by"] = actor_name
        completion_evidence["updated_at"] = now_iso

        messages = list(getattr(task, "messages", []) or [])
        message = (
            "Finalized covered task to Done using preserved coverage evidence."
        )
        covering_task_id = str(finalized_by.get("task_id", "") or "").strip()
        if covering_task_id:
            message += f" Covering task: {covering_task_id}."
        pr_url = str(finalized_by.get("pr_url", "") or "").strip()
        if pr_url:
            message += f" PR: {pr_url}."
        sha = str(finalized_by.get("sha", "") or "").strip()
        if sha:
            message += f" SHA: {sha}."
        if reason:
            message += f" Reason: {reason}"
        messages.append({
            "timestamp": time.time(),
            "action": "covered_by_finalized",
            "message": message,
            "agent_name": actor_name,
            "agent_id": actor_id,
            "agent_kind": actor_kind,
        })

        self.board_update_task(
            tid,
            completion_evidence=completion_evidence,
            messages=messages,
            status="",
        )
        self.board_move_task(tid, "Done", clear_status=True)

        refreshed = self.board_tasks.get(tid)
        return {
            "type": "task_coverage_finalized",
            "task_id": tid,
            "lane": str(getattr(refreshed, "lane", "") or ""),
            "covered_by": finalized_by,
            "message": message,
            "moved_to_done": True,
        }

    def board_pickup_architect_task(
            self,
            tid: str,
            *,
            architect_id: str,
            actor_name: str = "Torque",
            actor_kind: str = "architect",
            reason: str = "",
            source: str = "",
            authorization: dict | None = None) -> dict:
        """Claim a routed product-proposal task for an Architect with audit evidence."""
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task:
            raise ValueError("Task not found")

        def _clean(value) -> str:
            return str(value or "").strip()

        architect_id = _clean(architect_id)
        if not architect_id:
            raise ValueError("architect_id is required")
        actor_name = _clean(actor_name) or "Torque"
        actor_kind = _clean(actor_kind) or "architect"
        reason = _clean(reason)
        source = _clean(source)

        previous_assignment = {
            "assigned_architect_id": _clean(
                getattr(task, "assigned_architect_id", "") or ""
            ),
            "assigned_engineer_id": _clean(
                getattr(task, "assigned_engineer_id", "") or ""
            ),
            "agent_id": _clean(getattr(task, "agent_id", "") or ""),
            "created_by_architect_id": _clean(
                getattr(task, "created_by_architect_id", "") or ""
            ),
            "lane": _clean(getattr(task, "lane", "") or ""),
            "dispatch_state": _clean(
                getattr(task, "dispatch_state", "") or "queued"
            ),
        }
        previous_architect_id = previous_assignment["assigned_architect_id"]
        if previous_architect_id and previous_architect_id != architect_id:
            raise ValueError("Task is already assigned to another architect")

        now_iso = datetime.now(timezone.utc).isoformat()
        auth_payload = (
            copy.deepcopy(authorization)
            if isinstance(authorization, dict) else {}
        )
        pickup = {
            "picked_up_at": now_iso,
            "picked_up_by": actor_name,
            "picked_up_by_id": architect_id,
            "picked_up_by_kind": actor_kind,
            "previous_assignment": previous_assignment,
            "reason": reason,
            "source": source,
        }
        if auth_payload:
            pickup["authorization"] = auth_payload

        completion_evidence = _normalize_completion_evidence(
            getattr(task, "completion_evidence", {}) or {}
        )
        completion_evidence["status"] = (
            str(completion_evidence.get("status", "") or "").strip()
            or "evidence_attached"
        )
        completion_evidence["sources"] = _append_unique_string(
            completion_evidence.get("sources", []),
            "architect_pickup",
        )
        completion_evidence["architect_pickup"] = pickup
        completion_evidence["updated_by"] = actor_name
        completion_evidence["updated_at"] = now_iso

        messages = list(getattr(task, "messages", []) or [])
        message_parts = [f"Architect pickup: {actor_name} claimed this task."]
        if reason:
            message_parts.append(f"Reason: {reason}")
        if source:
            message_parts.append(f"Source: {source}")
        if auth_payload:
            auth_source = _clean(auth_payload.get("source", ""))
            route_id = _clean(auth_payload.get("route_message_id", ""))
            if auth_source or route_id:
                message_parts.append(
                    "Authorization: "
                    + ", ".join(
                        part for part in (
                            f"source={auth_source}" if auth_source else "",
                            f"route_message_id={route_id}" if route_id else "",
                        ) if part
                    )
                )
        message = " ".join(message_parts)
        messages.append({
            "timestamp": time.time(),
            "action": "architect_pickup",
            "message": message,
            "agent_name": actor_name,
            "agent_id": architect_id,
            "agent_kind": actor_kind,
        })

        self.board_update_task(
            tid,
            assigned_architect_id=architect_id,
            completion_evidence=completion_evidence,
            messages=messages,
        )
        refreshed = self.board_tasks.get(tid)
        return {
            "type": "task_picked_up",
            "task_id": tid,
            "assigned_architect_id": architect_id,
            "previous_assigned_architect_id": previous_architect_id,
            "already_assigned": previous_architect_id == architect_id,
            "lane": _clean(getattr(refreshed, "lane", "") or ""),
            "dispatch_state": _clean(
                getattr(refreshed, "dispatch_state", "") or "queued"
            ),
            "architect_pickup": pickup,
        }

    def evaluate_task_finalization(self, tid: str) -> dict:
        """Public canonical evaluator projection for all board/API callers."""
        return evaluate_finalization(self, self.resolve_task_alias(tid))

    def record_finalization_review(
            self, review_task_id: str, *, gate_id: str, verdict: str,
            has_blocking_issues: bool, required_follow_up_resolved: bool,
            boundary: str, executed: bool = True) -> dict:
        """Persist an explicit review execution record for a declared gate.

        Callers must supply typed verdict/follow-up facts; this routine never
        parses review prose and does not grant any authority itself.
        """
        review = self.board_tasks.get(self.resolve_task_alias(review_task_id))
        if not review:
            raise ValueError("Review task not found")
        root_id = str(getattr(review, "pipeline_root_id", "") or review.id)
        root = self.board_tasks.get(root_id)
        if not root:
            raise ValueError("Finalization root not found")
        declared = {gate["id"] for gate in getattr(root, "required_review_gates", [])
                    if isinstance(gate, dict) and gate.get("id")}
        if str(gate_id or "") not in declared:
            raise ValueError("Review gate is not declared on finalization root")
        evidence = dict(getattr(review, "completion_evidence", {}) or {})
        evidence["finalization_review"] = {
            "gate_id": str(gate_id), "verdict": str(verdict or "").lower(),
            "has_blocking_issues": bool(has_blocking_issues),
            "required_follow_up_resolved": bool(required_follow_up_resolved),
            "boundary": str(boundary or ""), "executed": bool(executed),
        }
        review.completion_evidence = evidence
        self._sync_finalization_projection(root)
        review.updated_at = datetime.now(timezone.utc).isoformat()
        root.updated_at = review.updated_at
        self._emit("task_upsert", **asdict(review))
        self._emit("task_upsert", **asdict(root))
        self._db_save_task(review)
        self._db_save_task(root)
        return self.evaluate_task_finalization(root.id)

    def record_merge_finalization(
            self, tid: str, *, mode: str, reference: str, reviewed_head_sha: str,
            merged_sha: str, origin_verified: bool, reviewed_tree: str,
            merged_tree: str, equal: bool) -> dict:
        """Attach guarded merge facts to the immutable policy boundary."""
        task = self.board_tasks.get(self.resolve_task_alias(tid))
        if not task:
            raise ValueError("Task not found")
        root_id = str(getattr(task, "pipeline_root_id", "") or task.id)
        root = self.board_tasks.get(root_id, task)
        if normalize_mode(getattr(root, "finalization_mode", "legacy")) != "merge":
            raise ValueError("Task does not use merge finalization")
        boundary = dict(getattr(root, "finalization_boundary", {}) or {})
        boundary["finalization"] = {
            "mode": str(mode or ""), "reference": str(reference or ""),
            "reviewed_head_sha": str(reviewed_head_sha or ""),
            "merged_sha": str(merged_sha or ""),
            "origin_verified": bool(origin_verified),
            "tree_equality": {"equal": bool(equal),
                              "reviewed_tree": str(reviewed_tree or ""),
                              "merged_tree": str(merged_tree or "")},
        }
        root.finalization_boundary = boundary
        result = self._sync_finalization_projection(root)
        root.updated_at = datetime.now(timezone.utc).isoformat()
        self._emit("task_upsert", **asdict(root))
        self._db_save_task(root)
        return result

    def _sync_finalization_projection(self, task: BoardTask) -> dict:
        root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
        root = self.board_tasks.get(root_id, task) if root_id else task
        if normalize_mode(getattr(root, "finalization_mode", "legacy")) == "legacy":
            return {"eligible": True, "mode": "legacy", "stage": "legacy", "boundary": "", "missing_gates": [], "explanations": []}
        result = evaluate_finalization(self, root)
        root.finalization_status = status_projection(result)
        return result

    def _finalization_done_allowed(self, task: BoardTask, *, caller: str) -> tuple[bool, dict]:
        """Evaluate/audit the root immediately before any Done mutation."""
        root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
        root = self.board_tasks.get(root_id, task) if root_id else task
        result = self._sync_finalization_projection(root)
        if normalize_mode(getattr(root, "finalization_mode", "legacy")) == "legacy":
            return True, result
        outcome = "success" if result.get("eligible") else "blocked"
        entry = audit_entry(result, caller=caller, outcome=outcome)
        audit = list(getattr(root, "finalization_audit", []) or [])
        # Repeated identical attempts are bounded and idempotent enough for
        # retries: do not append duplicate caller/outcome/boundary/code rows.
        if not audit or any(audit[-1].get(key) != entry.get(key) for key in ("caller", "outcome", "boundary", "missing_gates")):
            audit.append(entry)
            root.finalization_audit = audit[-40:]
        root.updated_at = datetime.now(timezone.utc).isoformat()
        self._emit("task_upsert", **asdict(root))
        self._db_save_task(root)
        return bool(result.get("eligible")), result

    def board_update_task(self, tid: str, **fields):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task:
            return
        self.ensure_board_task_persisted(tid)
        # Validate depends_on: strip self-refs, missing IDs, cycles
        if "depends_on" in fields:
            deps = fields["depends_on"]
            if not isinstance(deps, list):
                deps = []
            deps = [
                self.resolve_task_alias(d) for d in deps
                if self.resolve_task_alias(d) != tid
                and self.resolve_task_alias(d) in self.board_tasks
            ]
            if self._board_check_dep_cycle(tid, deps):
                return  # would create a cycle
            fields["depends_on"] = deps
        if "attachments" in fields:
            fields["attachments"] = normalize_attachments(
                fields["attachments"])
        if "artifacts" in fields:
            fields["artifacts"] = normalize_artifacts(fields["artifacts"])
        if "messages_thread" in fields:
            fields["messages_thread"] = _normalize_messages_thread(
                fields["messages_thread"]
            )
        if "dispatch_state" in fields:
            fields["dispatch_state"] = _normalize_task_dispatch_state(
                fields.get("dispatch_state")
            )
        old_dispatch_state = _normalize_task_dispatch_state(
            getattr(task, "dispatch_state", TASK_DISPATCH_STATE_QUEUED)
        )
        new_dispatch_state = fields.get("dispatch_state", old_dispatch_state)
        if (
                old_dispatch_state != TASK_DISPATCH_STATE_LIVE
                and new_dispatch_state == TASK_DISPATCH_STATE_LIVE
                and task.lane == "Backlog"
                and fields.get("lane", task.lane) == "Backlog"):
            live_lane = self._board_live_transition_lane(
                fields.get("group", task.group),
                agent_id=fields.get("agent_id", task.agent_id),
            )
            if live_lane:
                fields["lane"] = live_lane
        if "board_sync" in fields:
            fields["board_sync"] = _normalize_board_sync(fields["board_sync"])
        _normalize_verification_fields(fields)
        valid = set(BoardTask.__dataclass_fields__) - {"id", "slug", "created_at"}
        old_lane = task.lane
        new_lane = fields.get("lane", old_lane)
        lane_changed = "lane" in fields and new_lane != old_lane
        if "lane" in fields and new_lane not in self.board_lanes:
            return
        if lane_changed and new_lane == "Done" and not str(
                getattr(task, "pipeline_root_id", "") or "").strip():
            allowed, _result = self._finalization_done_allowed(
                task, caller="board_update_task"
            )
            if not allowed:
                return _result
        if lane_changed and (new_lane == ARCHIVED_LANE or old_lane == ARCHIVED_LANE):
            archive_position = fields.pop("position", None)
            fields.pop("lane", None)
            if fields:
                self.board_update_task(tid, **fields)
            if new_lane == ARCHIVED_LANE:
                self.board_archive_task(tid, position=archive_position)
            else:
                self.board_unarchive_task(
                    tid, lane=new_lane, position=archive_position
                )
            return
        for key, value in fields.items():
            if key in valid:
                setattr(task, key, value)
        if "task" in fields:
            task.slug = self._unique_task_slug(task.task, exclude_id=tid)
        # Status projection is derived, compact data rather than advisory prose.
        root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
        if root_id and root_id in self.board_tasks:
            self._sync_finalization_projection(self.board_tasks[root_id])
        elif normalize_mode(getattr(task, "finalization_mode", "legacy")) != "legacy":
            self._sync_finalization_projection(task)
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        task.updated_at = now_iso
        if lane_changed:
            task.lane_entered_at = now_iso
            if "position" not in fields:
                task.position = self._board_next_lane_position(
                    new_lane, exclude_id=tid
                )
        self._emit("task_upsert", **asdict(task))
        self._db_save_task(task)
        if lane_changed:
            self.evaluate_task_watches_for_task(task.id)
        if lane_changed and new_lane == "Done":
            self.board_cascade_done(tid, recompute=False)
        self.recompute_task_health()

    def board_remove_task(self, tid: str):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.pop(tid, None)
        if task:
            self._mark_task_health_dirty(task.parent_task_id)
            if (
                    task.agent_id
                    and self._task_lane_counts_as_agent_open(task.lane)
            ):
                self._mark_task_health_dirty(
                    *self._tasks_by_agent.get(task.agent_id, set())
                )
            self._unindex_task(task)
            self.auto_dispatch_queue_remove_task(tid)
            self._emit("task_remove", id=tid, group=task.group)
            self._db_delete_task(tid)
            # A deleted task must invalidate watches without exposing its title.
            self.reconcile_task_watches()
            # Clean up dependency references in other tasks
            for t in self.board_tasks.values():
                if tid in t.depends_on:
                    t.depends_on.remove(tid)
                    self._emit("task_upsert", **asdict(t))
                    self._db_save_task(t)
            if not self.cleanup_stale_boundary_successors():
                self.recompute_task_health()

    def board_move_task(self, tid: str, lane: str,
                        position: Optional[int] = None,
                        clear_status: bool = False):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task or lane not in self.board_lanes:
            return
        if lane == ARCHIVED_LANE and task.lane == ARCHIVED_LANE:
            if clear_status and task.status:
                self.board_update_task(tid, status="")
            return
        if lane == "Done" and task.lane != "Done" and not str(
                getattr(task, "pipeline_root_id", "") or "").strip():
            allowed, result = self._finalization_done_allowed(
                task, caller="board_move_task"
            )
            if not allowed:
                return result
        if clear_status:
            task.status = ""
        if lane == ARCHIVED_LANE:
            self.board_archive_task(tid, position=position)
            return
        if task.lane == ARCHIVED_LANE:
            self.board_unarchive_task(tid, lane=lane, position=position)
            return
        old_lane = task.lane
        self._board_apply_archive_state(
            task,
            lane=lane,
            archived_at="",
            archived_from_lane="",
            position=position,
            clear_attention=(lane == "Done"),
        )
        if lane == "Done":
            self.board_cascade_done(tid, recompute=False)
        self.recompute_task_health()

    def _append_engineer_message_expiry_note(self, task: BoardTask,
                                             timestamp: float) -> bool:
        if any(
                message.get("action") == "system"
                and message.get("message") == _ENGINEER_MESSAGE_EXPIRY_NOTE
                for message in (task.messages or [])):
            return False
        task.messages.append({
            "timestamp": timestamp,
            "action": "system",
            "message": _ENGINEER_MESSAGE_EXPIRY_NOTE,
            "agent_name": "Torque",
        })
        return True

    def _sync_pending_engineer_message_for_agent(
            self, agent_id: str, *, emit: bool = True) -> bool:
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return False
        cell = self.agents.get(agent_id)
        if not cell:
            return False
        pending = bool(self.agent_pending_engineer_reply_tasks(agent_id))
        if cell.pending_engineer_message == pending:
            return False
        cell.pending_engineer_message = pending
        if emit:
            self._emit_agent(cell)
        return True

    def _expire_engineer_message_task(self, task: BoardTask, *,
                                      emit: bool = True) -> bool:
        if (
                not task_is_engineer_message_followup(task)
                or board_task_is_closed(task)
        ):
            return False
        if "Done" not in self.board_lanes:
            return False

        now = datetime.now(timezone.utc)
        changed = self._append_engineer_message_expiry_note(
            task, now.timestamp()
        )
        if task.status:
            task.status = ""
            changed = True
        if task.lane != "Done" or task.archived_at or task.archived_from_lane:
            if emit:
                self._board_apply_archive_state(
                    task,
                    lane="Done",
                    archived_at="",
                    archived_from_lane="",
                    clear_attention=True,
                )
            else:
                old_lane = task.lane
                task.lane = "Done"
                task.archived_at = ""
                task.archived_from_lane = ""
                task.position = self._board_next_lane_position(
                    "Done", exclude_id=task.id
                )
                for label in ("torque:blocked", "torque:error"):
                    if label in task.labels:
                        task.labels.remove(label)
                task.updated_at = now.isoformat()
                if old_lane != "Done":
                    task.lane_entered_at = task.updated_at
                self._index_task(task)
                self._db_save_task(task)
            return True
        if changed:
            task.updated_at = now.isoformat()
            if emit:
                self._emit("task_upsert", **asdict(task))
            else:
                self._index_task(task)
            self._db_save_task(task)
            return True
        return False

    def expire_engineer_message_descendants(
            self, parent_task_id: str, *, emit: bool = True) -> int:
        """Expire open Engineer-message follow-ups under a resolved parent."""
        expired = 0
        reply_agent_ids: set[str] = set()
        for descendant in self.task_open_descendants(parent_task_id):
            if not task_is_engineer_message_followup(descendant):
                continue
            reply_agent_id = str(
                getattr(descendant, "reply_agent_id", "") or ""
            ).strip()
            if self._expire_engineer_message_task(descendant, emit=emit):
                expired += 1
                if reply_agent_id:
                    reply_agent_ids.add(reply_agent_id)

        for agent_id in reply_agent_ids:
            self._sync_pending_engineer_message_for_agent(agent_id, emit=emit)
        return expired

    def cleanup_resolved_engineer_message_followups(
            self, *, emit: bool = True) -> int:
        """Expire historical Engineer-message ghosts below resolved parents.

        This is intentionally idempotent: already-Done/archived follow-ups are
        not returned by ``task_open_descendants`` and therefore are not touched
        on subsequent runs.
        """
        expired = 0
        for task in sorted(
                self.board_tasks.values(),
                key=lambda task: (task.pipeline_depth, task.created_at, task.id),
        ):
            if task_counts_as_done(task):
                expired += self.expire_engineer_message_descendants(
                    task.id,
                    emit=emit,
                )
        return expired

    def board_cascade_done(self, tid: str, *,
                           recompute: bool = True) -> list[str]:
        """Complete ancestors whose entire descendant tree is done.

        Derived tasks suspend their parents in an active lane while follow-up
        work runs.  Once a descendant lands in Done and there are no open
        descendants left under an ancestor, that ancestor should also count as
        complete.  Keep this in the state layer so board moves, server-side
        reports, and other mutation paths all share the same cascade behavior.
        """
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task or not task_counts_as_done(task):
            return []
        expired = self.expire_engineer_message_descendants(tid)
        if task_suppresses_done_cascade(task):
            if expired and recompute:
                self.recompute_task_health()
            return []
        if "Done" not in self.board_lanes:
            return []

        changed: list[str] = []
        self._cascade_review_handoff_completions(task, changed)
        pid = task.parent_task_id
        while pid:
            parent = self.board_tasks.get(pid)
            if not parent:
                break
            next_pid = parent.parent_task_id
            if task_suppresses_done_cascade(parent):
                break
            if board_task_is_closed(parent):
                pid = next_pid
                continue
            if self.task_has_unresolved_descendants(parent.id):
                break
            allowed, _result = self._finalization_done_allowed(
                parent, caller="board_cascade_done"
            )
            if not allowed:
                break

            parent.status = ""
            self._board_apply_archive_state(
                parent,
                lane="Done",
                archived_at="",
                archived_from_lane="",
                clear_attention=True,
            )
            changed.append(parent.id)
            self._cascade_review_handoff_completions(parent, changed)
            pid = next_pid

        if (changed or expired) and recompute:
            self.recompute_task_health()
        return changed

    def board_archive_task(self, tid: str, *,
                           position: Optional[int] = None):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task or ARCHIVED_LANE not in self.board_lanes:
            return
        if task.lane == ARCHIVED_LANE:
            return
        archived_from_lane = task.lane
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        self._board_apply_archive_state(
            task,
            lane=ARCHIVED_LANE,
            archived_at=now_iso,
            archived_from_lane=archived_from_lane,
            position=position,
            unlink_agent=True,
        )
        if archived_from_lane == "Done":
            self.expire_engineer_message_descendants(tid)
        parent = self.board_tasks.get(task.parent_task_id)
        self._clear_parent_awaiting_input(parent, exclude_task_id=task.id)
        self.recompute_task_health()

    def board_archive_tasks(self, tids) -> list[str]:
        """Archive multiple board tasks as one atomic persisted operation."""
        requested: list[str] = []
        missing: list[str] = []
        seen: set[str] = set()
        for raw_tid in tids or []:
            tid = self.resolve_task_alias(str(raw_tid or ""))
            if not tid or tid not in self.board_tasks:
                missing.append(str(raw_tid or ""))
                continue
            if tid in seen:
                continue
            seen.add(tid)
            requested.append(tid)
        if missing:
            raise ValueError(
                "Task not found: " + ", ".join(tid or "(empty)" for tid in missing)
            )
        if not requested:
            return []
        if ARCHIVED_LANE not in self.board_lanes:
            raise ValueError("Archived lane is not configured")

        archive_targets = [
            tid for tid in requested
            if self.board_tasks[tid].lane != ARCHIVED_LANE
        ]
        if not archive_targets:
            return []

        before_tasks = {
            tid: copy.deepcopy(task)
            for tid, task in self.board_tasks.items()
        }
        before_agents = {
            aid: copy.deepcopy(agent)
            for aid, agent in self.agents.items()
        }
        before_delta_len = len(self._delta_ops)
        before_health_dirty = set(self._task_health_dirty)
        before_health_force_full = self._task_health_force_full
        existing_capture = self._current_critical_write_capture()
        before_capture = copy.deepcopy(existing_capture) if existing_capture else None
        temp_capture = bool(self.db and existing_capture is None)

        if temp_capture:
            self._critical_write_capture_var.set(CriticalWriteCapture(
                command_name="board_archive_tasks",
                idempotency_key="",
                request_hash="",
            ))

        try:
            for tid in archive_targets:
                self.board_archive_task(tid)

            if temp_capture:
                capture = self._current_critical_write_capture()
                self._critical_write_capture_var.set(None)
                tasks_to_save = list((capture.tasks if capture else {}).values())
                if tasks_to_save:
                    self.db.save_board_tasks(tasks_to_save)
        except Exception:
            if temp_capture:
                self._critical_write_capture_var.set(None)
            elif existing_capture is not None:
                self._critical_write_capture_var.set(before_capture)
            self.board_tasks = before_tasks
            self.agents = before_agents
            self._rebuild_task_indexes()
            self._delta_ops = self._delta_ops[:before_delta_len]
            self._task_health_dirty = before_health_dirty
            self._task_health_force_full = before_health_force_full
            raise

        return [
            tid for tid in archive_targets
            if self.board_tasks.get(tid)
            and self.board_tasks[tid].lane == ARCHIVED_LANE
        ]

    def board_unarchive_task(self, tid: str, *,
                             lane: str = "",
                             position: Optional[int] = None):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task or task.lane != ARCHIVED_LANE:
            return
        target_lane = lane or task.archived_from_lane or "Done"
        if target_lane == ARCHIVED_LANE or target_lane not in self.board_lanes:
            target_lane = "Done" if "Done" in self.board_lanes else self.board_lanes[0]
        self._board_apply_archive_state(
            task,
            lane=target_lane,
            archived_at="",
            archived_from_lane="",
            position=position,
            clear_attention=(target_lane == "Done"),
        )
        self.recompute_task_health()

    def board_reorder_task(self, tid: str, position: int):
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task:
            return
        lane_tasks = sorted(
            [t for t in self.board_tasks.values()
             if t.lane == task.lane and t.id != tid],
            key=lambda t: t.position,
        )
        lane_tasks.insert(min(position, len(lane_tasks)), task)
        for i, t in enumerate(lane_tasks):
            t.position = i
            self._emit("task_upsert", **asdict(t))
        for t in lane_tasks:
            self._db_save_task(t)

    def board_add_lane(self, name: str, position: Optional[int] = None):
        if not name or name in self.board_lanes:
            return
        if position is not None:
            self.board_lanes.insert(min(position, len(self.board_lanes)), name)
        else:
            self.board_lanes.append(name)
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()

    def board_rename_lane(self, old_name: str, new_name: str):
        if (old_name in _RESERVED_LANES or old_name not in self.board_lanes
                or not new_name or new_name in self.board_lanes):
            return
        idx = self.board_lanes.index(old_name)
        self.board_lanes[idx] = new_name
        for t in self.board_tasks.values():
            if t.lane == old_name:
                t.lane = new_name
                self._emit("task_upsert", **asdict(t))
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()
        for t in self.board_tasks.values():
            if t.lane == new_name:
                self._db_save_task(t)

    def board_remove_lane(self, name: str, move_tasks_to: str = ""):
        if (name in _RESERVED_LANES or name not in self.board_lanes
                or len(self.board_lanes) <= 1):
            return
        from datetime import datetime, timezone
        self.board_lanes.remove(name)
        target = move_tasks_to if move_tasks_to in self.board_lanes \
            else self.board_lanes[0]
        now_iso = datetime.now(timezone.utc).isoformat()
        max_pos = max(
            (t.position for t in self.board_tasks.values()
             if t.lane == target),
            default=-1,
        )
        for t in self.board_tasks.values():
            if t.lane == name:
                max_pos += 1
                t.lane = target
                t.position = max_pos
                t.updated_at = now_iso
                t.lane_entered_at = now_iso
                self._emit("task_upsert", **asdict(t))
                self._db_save_task(t)
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()

    def board_reorder_lanes(self, lanes: list[str]):
        if set(lanes) != set(self.board_lanes):
            return
        self.board_lanes = lanes
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()
