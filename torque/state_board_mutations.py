"""Board task and lane mutation behavior for MatrixState."""

from __future__ import annotations

import copy
import subprocess
import time
from dataclasses import asdict, fields
from datetime import datetime, timezone
from typing import Optional

from .artifacts import normalize_artifacts, normalize_attachments
from .task_ids import format_root_task_id, is_canonical_task_id, parse_task_id
from .task_content import compute_task_content_hash
from .task_amendment import (
    build_task_amendment_block,
    find_task_amendment,
    task_amendment_text_hash,
    validate_task_amendment,
)
from .finalization import audit_entry, evaluate_finalization, normalize_mode, status_projection
from .worktree_boundaries import code_boundary_done_status
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


def explicit_done_mainline_status(
        task: BoardTask, *, mainline: str = "") -> dict:
    """Classify an explicit close using the task's own durable merge SHA."""
    boundary = getattr(task, "worktree_boundary", {}) or {}
    if not isinstance(boundary, dict):
        boundary = {}
    repo_root = str(boundary.get("repo_root", "") or "").strip()
    mainline = (
        str(mainline or "").strip()
        or str(boundary.get("base_branch", "") or "").strip()
        or "main"
    )
    merge_sha = str(boundary.get("merge_commit_sha", "") or "").strip()
    branch = str(boundary.get("branch", "") or "").strip()
    result = {
        "required": True,
        "verified_in_mainline": False,
        "repo_root": repo_root,
        "mainline": mainline,
        "merge_commit_sha": merge_sha,
        "branch": branch,
        "reason": "missing_merge_sha",
        "error": "",
    }
    if not merge_sha:
        return result
    if not repo_root:
        result["reason"] = "missing_repo_root"
        return result
    try:
        proc = subprocess.run(
            [
                "git", "-C", repo_root, "merge-base", "--is-ancestor",
                merge_sha, mainline,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result["reason"] = "ancestry_check_error"
        result["error"] = str(exc)[:240]
        return result
    if proc.returncode == 0:
        result.update({
            "required": False,
            "verified_in_mainline": True,
            "reason": "merge_sha_in_mainline",
        })
    elif proc.returncode == 1:
        result["reason"] = "merge_sha_not_in_mainline"
    else:
        result["reason"] = "ancestry_check_error"
        result["error"] = proc.stderr.decode(
            errors="replace"
        ).strip()[:240]
    return result


class BoardMutationMixin:
    def _rehydrate_archived_task_artifacts(self, task: BoardTask) -> bool:
        """Restore a task's bodies before it returns to a live surface."""
        if not getattr(task, "_artifact_content_dehydrated", False):
            return True
        if not self.db:
            return False
        try:
            artifacts = self.db.load_board_task_artifacts(task.id)
        except Exception:
            return False
        if artifacts is None:
            return False
        task.artifacts = normalize_artifacts(artifacts)
        task._artifact_content_dehydrated = False
        return True

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
        # Establish authored identity before any finalization helper can emit
        # or persist this newly visible task.
        bt.task_content_hash = compute_task_content_hash(bt)
        # Creation is a Done attempt too. A root never enters Done until the
        # canonical admission guard accepts it; this includes opt-in legacy
        # review-cardinality declarations while empty legacy cards retain
        # their historical direct-Done behavior. Derived review tasks remain
        # free to close normally and drive their parent cascade.
        if lane == "Done" and self._is_finalization_root(bt):
            # Keep the task out of Done even transiently while we audit the
            # requested transition.  A client can never observe an ineligible
            # policy root in Done and then watch it be repaired.
            fallback_lane = "Backlog" if "Backlog" in self.board_lanes else self.board_lanes[0]
            bt.lane = fallback_lane
            bt.position = self._board_next_lane_position(fallback_lane, exclude_id=tid)
            if self._prepare_finalization_done(bt, caller="board_add_task"):
                bt.lane = "Done"
                bt.position = self._board_next_lane_position("Done", exclude_id=tid)
        self._refresh_finalization_root_projection(bt)
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
        self.emit_task_upsert(bt)
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
            self.board_move_task(
                tid, "Done", clear_status=True, allow_done_advisory=False
            )

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
        self.board_move_task(
            tid, "Done", clear_status=True, allow_done_advisory=False
        )

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
        """Claim an authorized Architect handoff with durable audit evidence."""
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
        matching_gate = next((
            gate for gate in getattr(root, "required_review_gates", [])
            if isinstance(gate, dict)
            and str(gate.get("id") or "") == str(gate_id or "")
            and str(gate.get("review_task_id") or "") == review.id
        ), None)
        if not matching_gate:
            raise ValueError("Review task is not the declared finalization gate")
        if verdict not in {"ship", "block", "needs_followup", "unknown"}:
            raise ValueError("Invalid structured review verdict")
        if not isinstance(has_blocking_issues, bool) or not isinstance(required_follow_up_resolved, bool):
            raise ValueError("Review blocker and follow-up fields must be booleans")
        if not isinstance(boundary, str) or not boundary.strip():
            raise ValueError("Review evidence boundary is required")
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
        self.emit_task_upsert(review)
        self.emit_task_upsert(root)
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
        self.emit_task_upsert(root)
        self._db_save_task(root)
        return result

    def _is_finalization_root_for_pipeline_root(
            self, task: BoardTask, pipeline_root_id: str) -> bool:
        root_id = str(pipeline_root_id or "").strip()
        # A nonempty root reference is a descendant relationship only when it
        # resolves to a real task.  Treat malformed/missing references as the
        # task's own root for finalization purposes; otherwise a policy root
        # could set ``pipeline_root_id='missing'`` and evade every Done guard.
        return not root_id or root_id == task.id or root_id not in self.board_tasks

    def _is_finalization_root(self, task: BoardTask) -> bool:
        return self._is_finalization_root_for_pipeline_root(
            task, getattr(task, "pipeline_root_id", "")
        )

    def _is_candidate_finalization_root(
            self, task: BoardTask, fields: dict) -> bool:
        """Resolve finalization ownership from the requested atomic update."""
        return self._is_finalization_root_for_pipeline_root(
            task, fields.get(
                "pipeline_root_id", getattr(task, "pipeline_root_id", "")
            )
        )

    def _refresh_finalization_root_projection(self, task: BoardTask) -> dict:
        """Persist/emit root status after any structurally relevant child change."""
        root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
        root = self.board_tasks.get(root_id, task) if root_id else task
        if normalize_mode(getattr(root, "finalization_mode", "legacy")) == "legacy":
            # Policy removal must retract the compact projection immediately;
            # otherwise a legacy card keeps a stale Reviewing/Ready badge.
            if getattr(root, "finalization_status", {}):
                root.finalization_status = {}
                root.updated_at = datetime.now(timezone.utc).isoformat()
                self.emit_task_upsert(root)
                self._db_save_task(root)
            return {"eligible": True, "mode": "legacy", "stage": "legacy",
                    "boundary": "", "missing_gates": [], "explanations": []}
        before = dict(getattr(root, "finalization_status", {}) or {})
        result = self._sync_finalization_projection(root)
        if root is not task or before != root.finalization_status:
            root.updated_at = datetime.now(timezone.utc).isoformat()
            self.emit_task_upsert(root)
            self._db_save_task(root)
        return result

    def _prepare_finalization_done(self, task: BoardTask, *, caller: str) -> bool:
        allowed, _result = self._finalization_done_allowed(task, caller=caller)
        return allowed

    def _sync_finalization_projection(self, task: BoardTask) -> dict:
        root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
        root = self.board_tasks.get(root_id, task) if root_id else task
        if normalize_mode(getattr(root, "finalization_mode", "legacy")) == "legacy":
            return {"eligible": True, "mode": "legacy", "stage": "legacy", "boundary": "", "missing_gates": [], "explanations": []}
        result = evaluate_finalization(self, root)
        root.finalization_status = status_projection(result)
        return result

    def _legacy_review_gate_applies(self, task: BoardTask) -> bool:
        """Return whether a legacy root still carries the review closeout gate.

        ``finalization_mode=legacy`` deliberately means that the newer
        finalization-policy contract does not apply.  It must not, however,
        erase the older mandatory feature-review contract.  ``requires_review``
        is stamped at dispatch for actions with required transitions; the
        feature implementation action is also recognized directly so a
        pre-dispatch root cannot evade its mandatory review by reaching Done
        through a child cascade.  Tasks with no action binding have no such
        mandatory-review action contract and remain eligible for ordinary
        cascade completion.
        """
        action_name = str(getattr(task, "action_name", "") or "").strip()
        if not action_name:
            return False
        return (
            bool(getattr(task, "requires_review", False))
            or action_name.lower() == "feature/implement"
        )

    def _legacy_review_gate_is_satisfied(self, task: BoardTask) -> bool:
        """Use the canonical Ship predicate, including its message fallback."""
        if not self._legacy_review_gate_applies(task):
            return True
        # Import lazily: server_review imports MatrixState, while this method
        # runs only after state construction.  Keeping the predicate canonical
        # prevents legacy cascade from drifting from verdict parsing/storage.
        from .server_review import _task_has_shipped_review_descendant
        return _task_has_shipped_review_descendant(self, task)

    def _legacy_review_cardinality_status(self, task: BoardTask) -> dict:
        """Evaluate the opt-in legacy cardinality declaration without prose."""
        from .server_review import _legacy_review_cardinality_status
        return _legacy_review_cardinality_status(self, task)

    def _finalization_done_allowed(self, task: BoardTask, *, caller: str) -> tuple[bool, dict]:
        """Evaluate/audit the root immediately before any Done mutation."""
        root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
        root = self.board_tasks.get(root_id, task) if root_id else task
        result = self._sync_finalization_projection(root)
        code_result = self._code_boundary_done_allowed(root)
        if code_result is not None:
            return False, code_result
        if normalize_mode(getattr(root, "finalization_mode", "legacy")) == "legacy":
            cardinality = self._legacy_review_cardinality_status(root)
            if cardinality["declared_count"] and not cardinality["eligible"]:
                from .server_review import _legacy_review_cardinality_error
                return False, {
                    "eligible": False,
                    "mode": "legacy",
                    "stage": "reviewing",
                    "boundary": "",
                    "missing_gates": ["legacy_review_cardinality_shortfall"],
                    "explanations": [
                        _legacy_review_cardinality_error(cardinality)
                    ],
                    **cardinality,
                }
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
        self.emit_task_upsert(root)
        self._db_save_task(root)
        return bool(result.get("eligible")), result

    def _code_boundary_done_allowed(self, root: BoardTask) -> dict | None:
        """Return the durable code-boundary rejection, if the root has one.

        This shared admission primitive deliberately reads only persisted task
        boundaries.  It is used for ordinary, cascade, and detached candidate
        Done evaluations so an atomic policy update cannot bypass the gate.
        """
        code_gate = code_boundary_done_status(self.board_get_chain(root.id))
        if code_gate["eligible"]:
            return None
        return {
            "eligible": False,
            "mode": "code_boundary",
            "stage": "awaiting_merge",
            "boundary": "",
            "missing_gates": ["code_boundary_not_durably_merged"],
            "explanations": [
                "Code-bearing or unclassified task boundary lacks durable "
                "merged status and merge SHA."
            ],
            "code_boundary": code_gate,
        }

    def _candidate_finalization_done_allowed(
            self, task: BoardTask, fields: dict, *, caller: str) -> tuple[bool, dict]:
        """Evaluate an atomic policy/Done update before mutating the root.

        Board updates can carry a lane and the explicit finalization contract in
        one request.  Evaluating the persisted task first would see its legacy
        mode and allow a subsequent policy write to leave an ineligible root in
        Done.  Evaluate a detached candidate in the task index instead, then
        only apply the request when that exact post-update root is eligible.
        """
        candidate = copy.deepcopy(task)
        valid = set(BoardTask.__dataclass_fields__) - {"id", "slug", "created_at"}
        for key, value in fields.items():
            if key in valid:
                setattr(candidate, key, value)

        # ``evaluate_finalization`` resolves roots through ``board_tasks``.
        # Temporarily substitute only the detached candidate; it is a pure
        # evaluation and never emits or persists the candidate state.
        self.board_tasks[task.id] = candidate
        try:
            result = evaluate_finalization(self, candidate)
            code_result = self._code_boundary_done_allowed(candidate)
        finally:
            self.board_tasks[task.id] = task

        if code_result is not None:
            result = code_result
        if result.get("eligible"):
            return True, result

        # A rejected candidate still records the attempted finalization with
        # the candidate mode/boundary.  The legacy task itself remains legacy
        # and is never transiently written with the requested policy.
        entry = audit_entry(result, caller=caller, outcome="blocked")
        audit = list(getattr(task, "finalization_audit", []) or [])
        if not audit or any(
                audit[-1].get(key) != entry.get(key)
                for key in ("caller", "outcome", "boundary", "missing_gates")):
            audit.append(entry)
            task.finalization_audit = audit[-40:]
        task.updated_at = datetime.now(timezone.utc).isoformat()
        self.emit_task_upsert(task)
        self._db_save_task(task)
        return False, result

    def board_amend_task(
        self,
        tid: str,
        *,
        amendment: str,
        amendment_id: str,
        actor_id: str,
        expected_task_content_hash: str,
        added_at: str,
    ) -> dict:
        """Atomically compare-and-append one immutable attributed amendment."""
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task:
            return {"type": "error", "reason": "task_not_found"}
        validation_error = validate_task_amendment(
            amendment, amendment_id
        )
        if validation_error:
            return {
                "type": "error",
                "reason": "invalid_task_amendment",
                "message": validation_error,
            }
        if not str(actor_id or "").strip():
            return {
                "type": "error",
                "reason": "invalid_task_amendment",
                "message": "actor_id is required",
            }
        if not str(added_at or "").strip():
            return {
                "type": "error",
                "reason": "invalid_task_amendment",
                "message": "added_at is required",
            }
        self.ensure_board_task_persisted(tid)
        current_hash = compute_task_content_hash(task)
        # Normalize stale loaded values before evaluating either retry or CAS.
        task.task_content_hash = current_hash
        existing = find_task_amendment(task.description, amendment_id)
        if existing:
            if existing.get("amendment_sha256") != task_amendment_text_hash(
                    amendment):
                return {
                    "type": "error",
                    "reason": "amendment_id_conflict",
                    "task_id": tid,
                    "amendment_id": amendment_id,
                    "message": (
                        "amendment_id is already attached to this task with "
                        "different amendment text"
                    ),
                }
            return {
                "type": "ok",
                "task_id": tid,
                "amendment_id": amendment_id,
                "task_content_hash": current_hash,
                "deduped": True,
            }
        if expected_task_content_hash != current_hash:
            return {
                "type": "error",
                "reason": "task_content_hash_mismatch",
                "task_id": tid,
                "expected_task_content_hash": expected_task_content_hash,
                "current_task_content_hash": current_hash,
                "next_step": (
                    "Re-read the task record and re-amend against the current "
                    "hash."
                ),
            }
        block = build_task_amendment_block(
            amendment=amendment,
            amendment_id=amendment_id,
            actor_id=actor_id,
            prior_task_content_hash=current_hash,
            added_at=added_at,
        )
        original_description = task.description
        task.description = original_description + block
        # The authored hash changes normally, invalidating active grants while
        # preserving completed verdict evidence against its prior pinned hash.
        task.task_content_hash = compute_task_content_hash(task)
        task.updated_at = added_at
        self.emit_task_upsert(task)
        self._db_save_task(task)
        self.recompute_task_health()
        return {
            "type": "ok",
            "task_id": tid,
            "amendment_id": amendment_id,
            "prior_task_content_hash": current_hash,
            "task_content_hash": task.task_content_hash,
            "deduped": False,
        }

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
        policy_fields = {
            "finalization_mode", "required_review_gates", "finalization_boundary",
        }
        policy_update = bool(policy_fields & set(fields))
        # Projection ownership may change in the same mutation as policy
        # removal (for example, when a former root is reparented).  Remember
        # the transition against the task itself before resolving the
        # post-update root, so a legacy card can never retain its old policy
        # badge merely because it now points at another root.
        retract_task_finalization_projection = (
            "finalization_mode" in fields
            and normalize_mode(getattr(task, "finalization_mode", "legacy")) != "legacy"
            and normalize_mode(fields.get("finalization_mode")) == "legacy"
        )
        # Root ownership is part of the candidate transaction.  Checking the
        # persisted child first would let one atomic request replace its valid
        # root with ``self``/an unknown root, add a policy, and enter Done
        # without any evaluation or audit.
        candidate_done_check = (
            new_lane == "Done"
            and (policy_update or "pipeline_root_id" in fields)
            and self._is_candidate_finalization_root(task, fields)
        )
        candidate_result = None
        if candidate_done_check:
            allowed, candidate_result = self._candidate_finalization_done_allowed(
                task, fields, caller="board_update_task"
            )
            if not allowed:
                return candidate_result
        elif lane_changed and new_lane == "Done" and self._is_finalization_root(task):
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
        # An explicit artifact replacement is the one write that may alter
        # artifact JSON. Clear the safeguard only after every validation and
        # archive-transition early return above has been passed.
        if "artifacts" in fields:
            task._artifact_content_dehydrated = False
        if lane_changed and new_lane == "Done":
            # Candidate/finalization/code-boundary admission has succeeded.
            # Expire transient coordination descendants at the last safe
            # point before the authored Done mutation becomes visible.
            self.expire_engineer_message_descendants(tid)
        for key, value in fields.items():
            if key in valid:
                setattr(task, key, value)
        if retract_task_finalization_projection:
            task.finalization_status = {}
        if candidate_result is not None:
            # The candidate was accepted and is now the actual task.  Record
            # the successful Done admission after applying it so its audit and
            # projection persist with the non-legacy contract.
            entry = audit_entry(candidate_result, caller="board_update_task",
                                outcome="success")
            audit = list(getattr(task, "finalization_audit", []) or [])
            if not audit or any(
                    audit[-1].get(key) != entry.get(key)
                    for key in ("caller", "outcome", "boundary", "missing_gates")):
                audit.append(entry)
                task.finalization_audit = audit[-40:]
        if "task" in fields:
            task.slug = self._unique_task_slug(task.task, exclude_id=tid)
        # All authored fields are now applied. Establish their identity before
        # finalization projection can synchronously emit/persist this task.
        task.task_content_hash = compute_task_content_hash(task)
        # Status projection is derived, compact data rather than advisory prose.
        self._refresh_finalization_root_projection(task)
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        task.updated_at = now_iso
        if lane_changed:
            task.lane_entered_at = now_iso
            if "position" not in fields:
                task.position = self._board_next_lane_position(
                    new_lane, exclude_id=tid
                )
        self.emit_task_upsert(task)
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
            self._refresh_finalization_root_projection(task)
            self.auto_dispatch_queue_remove_task(tid)
            self._emit("task_remove", id=tid, group=task.group)
            self._db_delete_task(tid)
            # A deleted task must invalidate watches without exposing its title.
            self.reconcile_task_watches()
            # Clean up dependency references in other tasks
            for t in self.board_tasks.values():
                if tid in t.depends_on:
                    t.depends_on.remove(tid)
                    self.emit_task_upsert(t)
                    self._db_save_task(t)
            if not self.cleanup_stale_boundary_successors():
                self.recompute_task_health()

    def board_move_task(self, tid: str, lane: str,
                        position: Optional[int] = None,
                        clear_status: bool = False,
                        allow_done_advisory: bool = True,
                        acknowledge_unmerged: bool = False):
        """Apply an explicit lane move, returning any non-blocking Done finding.

        Internal automatic closeout callers pass ``allow_done_advisory=False``
        so their existing eligibility rules remain admission gates.
        """
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task or lane not in self.board_lanes:
            return
        if lane == ARCHIVED_LANE and task.lane == ARCHIVED_LANE:
            if clear_status and task.status:
                self.board_update_task(tid, status="")
            return
        advisory = None
        if lane == "Done" and task.lane != "Done" and self._is_finalization_root(task):
            allowed, result = self._finalization_done_allowed(
                task, caller="board_move_task"
            )
            if not allowed:
                if task.lane == ARCHIVED_LANE or not allow_done_advisory:
                    return result
                acknowledgement = explicit_done_mainline_status(
                    task,
                    mainline=str(
                        getattr(self, "boot_mainline_branch", "") or ""
                    ).strip(),
                )
                if acknowledgement["required"] and not acknowledge_unmerged:
                    blocking = result.get("code_boundary", {}).get(
                        "blocking", []
                    )
                    blocking_ids = [
                        str(item.get("task_id", "") or "").strip()
                        for item in blocking if isinstance(item, dict)
                    ]
                    referenced = [
                        self.board_tasks.get(task_id)
                        for task_id in blocking_ids
                        if self.board_tasks.get(task_id)
                    ]
                    boundary_refs = []
                    for referenced_task in referenced:
                        boundary = getattr(
                            referenced_task, "worktree_boundary", {}
                        ) or {}
                        if not isinstance(boundary, dict):
                            continue
                        boundary_refs.append({
                            "task_id": referenced_task.id,
                            "branch": str(
                                boundary.get("branch", "") or ""
                            ).strip(),
                            "commit_sha": str(
                                boundary.get("commit_sha", "") or ""
                            ).strip(),
                        })
                    acknowledgement["blocking"] = boundary_refs
                    ref = next((
                        item.get("branch") or item.get("commit_sha")
                        for item in boundary_refs
                        if item.get("branch") or item.get("commit_sha")
                    ), acknowledgement.get("branch")
                       or acknowledgement.get("merge_commit_sha")
                       or task.id)
                    return {
                        "type": "task_move_acknowledgement_required",
                        "task_id": task.id,
                        "message": (
                            "Closing this task will leave unmerged code at "
                            f"{ref}. Acknowledge that the code will not be "
                            "merged here or will be handled another way."
                        ),
                        "acknowledgement": acknowledgement,
                        "advisory": result,
                    }
                # Explicit operator intent wins over finalization bookkeeping.
                # Keep evaluating/auditing the evidence, but return the finding
                # as an advisory after the authored lane mutation succeeds.
                advisory = result
        if clear_status:
            task.status = ""
        if lane == ARCHIVED_LANE:
            self.board_archive_task(tid, position=position)
            return
        if task.lane == ARCHIVED_LANE:
            self.board_unarchive_task(tid, lane=lane, position=position)
            return
        if lane == "Done" and task.lane != "Done":
            # Finalization/code-boundary admission has passed. Expire only
            # transient coordination descendants at the last safe point
            # before committing the parent to Done.
            self.expire_engineer_message_descendants(tid)
        old_lane = task.lane
        self._board_apply_archive_state(
            task,
            lane=lane,
            archived_at="",
            archived_from_lane="",
            position=position,
            clear_attention=(lane == "Done"),
        )
        self._refresh_finalization_root_projection(task)
        if lane == "Done":
            self.board_cascade_done(tid, recompute=False)
        self.recompute_task_health()
        return advisory

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
        if self._is_finalization_root(task):
            allowed, _result = self._finalization_done_allowed(
                task, caller="expire_engineer_message_task"
            )
            if not allowed:
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
                self.emit_task_upsert(task)
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
        if task_suppresses_done_cascade(task):
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
            if self.task_has_completion_blocking_descendants(parent.id):
                break
            # Legacy finalization intentionally does not activate the newer
            # six-condition policy.  Cascade is nevertheless the one root-Done
            # writer that does not first pass through the mandatory review
            # command gate, so enforce that pre-existing contract here.
            if normalize_mode(getattr(
                    parent, "finalization_mode", "legacy")) == "legacy":
                cardinality = self._legacy_review_cardinality_status(parent)
                if cardinality["declared_count"]:
                    if not cardinality["eligible"]:
                        break
                elif not self._legacy_review_gate_is_satisfied(parent):
                    break
            allowed, _result = self._finalization_done_allowed(
                parent, caller="board_cascade_done"
            )
            if not allowed:
                break

            self.expire_engineer_message_descendants(parent.id)
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

        if changed and recompute:
            self.recompute_task_health()
        return changed

    def board_recheck_done_cascade(self, tid: str, *,
                                   recompute: bool = True) -> list[str]:
        """Re-evaluate a tree after an ancestor's Done-gate input changes.

        ``board_cascade_done`` is intentionally driven by a descendant that
        has already reached Done.  Some of its root-admission inputs (most
        notably durable merge evidence) are recorded later, after that normal
        trigger correctly refused the root.  Seed the existing cascade from
        every completed descendant rather than duplicating its admission or
        descendant checks here.  This keeps the recheck a no-op until all
        descendants are resolved and preserves the canonical Done gate.
        """
        tid = self.resolve_task_alias(tid)
        task = self.board_tasks.get(tid)
        if not task:
            return []
        if task_counts_as_done(task):
            return self.board_cascade_done(tid, recompute=recompute)

        completed_descendants: list[BoardTask] = []
        stack = self.board_get_children(task.id)
        stack.extend(self.review_handoff_followups(task.id))
        seen = set()
        while stack:
            current = stack.pop()
            if current.id in seen:
                continue
            seen.add(current.id)
            stack.extend(self.board_get_children(current.id))
            if self._is_review_handoff_source(current):
                stack.extend(self.review_handoff_followups(current.id))
            if task_counts_as_done(current):
                completed_descendants.append(current)

        changed: list[str] = []
        for descendant in sorted(
                completed_descendants,
                key=lambda item: (-item.pipeline_depth, item.created_at, item.id),
        ):
            for changed_id in self.board_cascade_done(
                    descendant.id, recompute=False):
                if changed_id not in changed:
                    changed.append(changed_id)
        if changed and recompute:
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
        self._refresh_finalization_root_projection(task)
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
        if target_lane == "Done" and self._is_finalization_root(task):
            allowed, result = self._finalization_done_allowed(
                task, caller="board_unarchive_task"
            )
            if not allowed:
                return result
        # get_task_detail is intentionally in-memory only. Rehydrate only
        # after every rejection-capable target/finalization check has passed,
        # immediately before a task can return to a live surface. A denied
        # unarchive therefore remains fully dehydrated in memory.
        if not self._rehydrate_archived_task_artifacts(task):
            return {
                "type": "error",
                "message": "Unable to restore archived task artifacts",
            }
        self._board_apply_archive_state(
            task,
            lane=target_lane,
            archived_at="",
            archived_from_lane="",
            position=position,
            clear_attention=(target_lane == "Done"),
        )
        self._refresh_finalization_root_projection(task)
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
            self.emit_task_upsert(t)
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
                self.emit_task_upsert(t)
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()
        for t in self.board_tasks.values():
            if t.lane == new_name:
                self._db_save_task(t)

    def board_remove_lane(self, name: str, move_tasks_to: str = ""):
        if (name in _RESERVED_LANES or name not in self.board_lanes
                or len(self.board_lanes) <= 1):
            return
        remaining_lanes = [lane for lane in self.board_lanes if lane != name]
        target = move_tasks_to if move_tasks_to in remaining_lanes \
            else remaining_lanes[0]
        moving_tasks = [task for task in self.board_tasks.values()
                        if task.lane == name]
        # Preflight the complete batch before removing its source lane.  This
        # keeps a denied policy root in its truthful lane and avoids a partial
        # lane-removal mutation with stranded cards.
        blocked: list[dict] = []
        policy_roots = [task for task in moving_tasks
                        if self._is_finalization_root(task)]
        if target == "Done":
            for task in policy_roots:
                result = evaluate_finalization(self, task)
                if not result.get("eligible"):
                    # The canonical guard appends the bounded blocked audit and
                    # refreshes the compact projection without moving anything.
                    _allowed, audited = self._finalization_done_allowed(
                        task, caller="board_remove_lane"
                    )
                    blocked.append({
                        "task_id": task.id,
                        "missing_gates": list(audited.get("missing_gates", [])),
                    })
        if blocked:
            return {
                "type": "finalization_blocked",
                "lane": name,
                "target_lane": target,
                "blocked": blocked,
            }

        if target == "Done":
            # Success audits occur only for the atomic batch that is actually
            # about to enter Done, never for a lane-removal attempt later
            # refused because another root was ineligible.
            for task in policy_roots:
                allowed, _result = self._finalization_done_allowed(
                    task, caller="board_remove_lane"
                )
                if not allowed:  # defensive re-evaluation before mutation
                    return {"type": "finalization_blocked", "lane": name,
                            "target_lane": target, "blocked": [{
                                "task_id": task.id,
                                "missing_gates": list(_result.get("missing_gates", [])),
                            }]}

        from datetime import datetime, timezone
        self.board_lanes.remove(name)
        now_iso = datetime.now(timezone.utc).isoformat()
        max_pos = max(
            (t.position for t in self.board_tasks.values()
             if t.lane == target),
            default=-1,
        )
        for task in moving_tasks:
            max_pos += 1
            task.lane = target
            task.position = max_pos
            task.updated_at = now_iso
            task.lane_entered_at = now_iso
            self._refresh_finalization_root_projection(task)
            self.emit_task_upsert(task)
            self._db_save_task(task)
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()
        return {"type": "lane_removed", "lane": name, "target_lane": target}

    def board_reorder_lanes(self, lanes: list[str]):
        if set(lanes) != set(self.board_lanes):
            return
        self.board_lanes = lanes
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()
