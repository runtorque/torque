"""Verification, deliverable, proposal-finalization, and merge evidence helpers."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from .artifacts import normalize_artifacts
from .config import log
from .services.worktrees import _base_match_from_result, _sha_equal
from .state import MatrixState, task_counts_as_done, task_is_closed


def _apply_verification_report(task, payload, actor_name, save_task,
                               *, root_task=None, timestamp=None):
    """Apply a verification checkpoint update to a task and optional root."""
    if not task:
        return "", None

    from datetime import datetime, timezone

    summary = dict(task.verification_summary or {})
    if "tests_run" in payload:
        tests_run = str(payload.get("tests_run", "") or "").strip()
        if tests_run:
            summary["tests_run"] = tests_run
        else:
            summary.pop("tests_run", None)
    if "manual_smoke_done" in payload:
        summary["manual_smoke_done"] = bool(
            payload.get("manual_smoke_done")
        )
    smoke_status = str(payload.get("smoke_status", "") or "").strip()
    if smoke_status in {"passed", "failed"}:
        summary["manual_smoke_done"] = True
    if "deploy_needed" in payload:
        summary["deploy_needed"] = bool(
            payload.get("deploy_needed")
        )
    if "deploy_attempted" in payload:
        summary["deploy_attempted"] = bool(
            payload.get("deploy_attempted")
        )
    if "human_validation_pending" in payload:
        human_pending = str(
            payload.get("human_validation_pending", "") or ""
        ).strip()
        if human_pending:
            summary["human_validation_pending"] = human_pending
        else:
            summary.pop("human_validation_pending", None)
    if "isolated_rerun_evidence" in payload:
        isolated_rerun = str(
            payload.get("isolated_rerun_evidence", "") or ""
        ).strip()
        if isolated_rerun:
            summary["isolated_rerun_evidence"] = isolated_rerun
        else:
            summary.pop("isolated_rerun_evidence", None)
    if "test_outcome" in payload:
        test_outcome = str(payload.get("test_outcome", "") or "").strip()
        if test_outcome in {
            "passed",
            "full_suite_passed",
            "full_suite_attempted",
            "unrelated_flake_accepted",
            "narrower_suite_accepted",
            "failed",
        }:
            summary["test_outcome"] = test_outcome
        else:
            summary.pop("test_outcome", None)
    if "reviewer_acceptance" in payload:
        reviewer_acceptance = str(
            payload.get("reviewer_acceptance", "") or ""
        ).strip()
        if reviewer_acceptance in {
            "accepted_flake_evidence",
            "accepted_narrower_suite",
        }:
            summary["reviewer_acceptance"] = reviewer_acceptance
        else:
            summary.pop("reviewer_acceptance", None)
    for key in (
        "full_suite_attempted",
        "unrelated_flake_accepted",
        "live_smoke_pending",
    ):
        if key in payload:
            summary[key] = bool(payload.get(key))

    if "verification_mode" in payload:
        mode = str(payload.get("verification_mode", "") or "").strip()
        task.verification_mode = (
            mode if mode in {"", "deploy", "restart"} else ""
        )

    verification_state = None
    if "verification_state" in payload:
        verify_state = str(
            payload.get("verification_state", "") or ""
        ).strip()
        verification_state = (
            verify_state if verify_state in {
                "", "pending", "attempted", "passed", "failed"
            } else ""
        )
    elif smoke_status in {"passed", "failed"}:
        verification_state = smoke_status
    elif "deploy_attempted" in payload and payload.get("deploy_attempted"):
        verification_state = "attempted"
    if verification_state is not None:
        task.verification_state = verification_state
        if verification_state == "passed":
            summary.pop("human_validation_pending", None)
            summary.pop("deploy_needed", None)
            summary.pop("live_smoke_pending", None)
            if summary.get("full_suite_attempted"):
                summary.setdefault("test_outcome", "full_suite_passed")
            elif summary.get("tests_run"):
                summary.setdefault("test_outcome", "passed")
        elif verification_state == "failed" and summary.get("tests_run"):
            summary.setdefault("test_outcome", "failed")
    if summary.get("unrelated_flake_accepted"):
        summary.setdefault("test_outcome", "unrelated_flake_accepted")
    elif (
        summary.get("full_suite_attempted")
        and not summary.get("test_outcome")
    ):
        summary["test_outcome"] = "full_suite_attempted"

    if "verification_notes" in payload:
        task.verification_notes = str(
            payload.get("verification_notes", "") or ""
        ).strip()

    task.verification_summary = summary
    task.verification_updated_at = (
        timestamp
        or datetime.now(timezone.utc).isoformat()
    )
    task.verification_updated_by = actor_name

    parts = []
    if task.verification_state:
        parts.append(f"state={task.verification_state}")
    if task.verification_mode:
        parts.append(f"mode={task.verification_mode}")
    if summary.get("tests_run"):
        parts.append(f"tests={summary['tests_run']}")
    if summary.get("test_outcome"):
        parts.append(f"test outcome={summary['test_outcome']}")
    if summary.get("full_suite_attempted"):
        parts.append("full suite attempted")
    if summary.get("unrelated_flake_accepted"):
        parts.append("unrelated flake accepted")
    if summary.get("isolated_rerun_evidence"):
        parts.append("isolated rerun=" + summary["isolated_rerun_evidence"])
    if summary.get("reviewer_acceptance"):
        parts.append("reviewer acceptance=" + summary["reviewer_acceptance"])
    if summary.get("manual_smoke_done"):
        parts.append("manual smoke done")
    if summary.get("live_smoke_pending"):
        parts.append("live smoke pending")
    if summary.get("deploy_needed"):
        parts.append("deploy needed")
    if "deploy_attempted" in summary:
        parts.append(
            "deploy attempted" if summary.get("deploy_attempted")
            else "deploy not attempted"
        )
    if summary.get("human_validation_pending"):
        parts.append(
            "human validation="
            + summary["human_validation_pending"]
        )
    if task.verification_notes:
        parts.append(f"notes={task.verification_notes}")

    msg = "Verification updated"
    if parts:
        msg += ": " + "; ".join(parts)

    save_task(task)

    if root_task:
        root_task.verification_mode = task.verification_mode
        root_task.verification_state = task.verification_state
        root_task.verification_notes = task.verification_notes
        root_task.verification_updated_at = task.verification_updated_at
        root_task.verification_updated_by = task.verification_updated_by
        root_task.verification_summary = dict(summary)
        save_task(root_task)

    return msg, root_task


_COMPLETION_EVIDENCE_VERSION = 1


def _completion_evidence_text(value, *, limit: int = 2000) -> str:
    text = str(value or "").strip()
    if limit > 0 and len(text) > limit:
        return text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def _task_verification_evidence(task) -> dict:
    if not task:
        return {}
    summary = getattr(task, "verification_summary", {}) or {}
    if not isinstance(summary, dict):
        summary = {}
    evidence = {}
    state_value = _completion_evidence_text(
        getattr(task, "verification_state", ""))
    mode_value = _completion_evidence_text(
        getattr(task, "verification_mode", ""))
    notes_value = _completion_evidence_text(
        getattr(task, "verification_notes", ""))
    if state_value:
        evidence["state"] = state_value
    if mode_value:
        evidence["mode"] = mode_value
    normalized_summary = {}
    for key in (
        "tests_run",
        "human_validation_pending",
        "isolated_rerun_evidence",
        "test_outcome",
        "reviewer_acceptance",
    ):
        value = _completion_evidence_text(summary.get(key, ""))
        if value:
            normalized_summary[key] = value
    for key in (
        "manual_smoke_done",
        "deploy_needed",
        "deploy_attempted",
        "full_suite_attempted",
        "unrelated_flake_accepted",
        "live_smoke_pending",
    ):
        if key in summary:
            normalized_summary[key] = bool(summary.get(key))
    if normalized_summary:
        evidence["summary"] = normalized_summary
    if notes_value:
        evidence["notes"] = notes_value
    updated_at = _completion_evidence_text(
        getattr(task, "verification_updated_at", ""))
    updated_by = _completion_evidence_text(
        getattr(task, "verification_updated_by", ""))
    if updated_at:
        evidence["updated_at"] = updated_at
    if updated_by:
        evidence["updated_by"] = updated_by
    return evidence


def _task_artifact_evidence(task, *, limit: int = 12) -> dict:
    if not task:
        return {}
    candidates = []
    for artifact in normalize_artifacts(getattr(task, "artifacts", []) or []):
        candidates.append({
            "type": _completion_evidence_text(
                artifact.get("type", "artifact"), limit=80),
            "title": _completion_evidence_text(
                artifact.get("title")
                or artifact.get("filename")
                or artifact.get("type", "artifact"),
                limit=160,
            ),
            "filename": _completion_evidence_text(
                artifact.get("filename", ""), limit=160),
            "path": _completion_evidence_text(
                artifact.get("path", ""), limit=400),
            "summary": _completion_evidence_text(
                artifact.get("summary", ""), limit=500),
        })
    for attachment in getattr(task, "attachments", []) or []:
        if isinstance(attachment, dict):
            filename = attachment.get("filename") or attachment.get("path") or ""
            path = attachment.get("path", "")
        else:
            filename = str(attachment or "")
            path = filename
        if not filename and not path:
            continue
        candidates.append({
            "type": "attachment",
            "title": _completion_evidence_text(filename or path, limit=160),
            "filename": _completion_evidence_text(filename, limit=160),
            "path": _completion_evidence_text(path, limit=400),
            "summary": "",
        })
    if not candidates:
        return {}
    return {
        "count": len(candidates),
        "items": candidates[:limit],
    }


def _completion_evidence_status(*, verification=None, merge=None) -> str:
    verification = verification or {}
    merge = merge or {}
    verified = (
        str(verification.get("state") or "").strip() == "passed"
        or bool(merge.get("origin_verified"))
    )
    return "verified" if verified else "evidence_attached"


def _merge_completion_evidence(existing, update: dict) -> dict:
    evidence = dict(existing or {}) if isinstance(existing, dict) else {}
    sources = []
    for source in evidence.get("sources") or []:
        source = str(source or "").strip()
        if source and source not in sources:
            sources.append(source)
    for source in update.get("sources") or []:
        source = str(source or "").strip()
        if source and source not in sources:
            sources.append(source)

    for key in ("completion", "verification", "artifacts", "merge", "review"):
        if key in update:
            evidence[key] = update[key]
    evidence["version"] = _COMPLETION_EVIDENCE_VERSION
    evidence["sources"] = sources
    evidence["updated_at"] = update.get("updated_at") \
        or datetime.now(timezone.utc).isoformat()
    if update.get("updated_by"):
        evidence["updated_by"] = update["updated_by"]

    status = _completion_evidence_status(
        verification=evidence.get("verification"),
        merge=evidence.get("merge"),
    )
    evidence["status"] = status
    evidence["verified"] = status == "verified"
    return evidence if sources else {}


def _save_completion_evidence_task(state: MatrixState, task) -> None:
    task.updated_at = datetime.now(timezone.utc).isoformat()
    state._emit("task_upsert", **asdict(task))
    state._db_save_task(task)


def _task_tests_run_completion_evidence(task) -> str:
    if not task:
        return ""
    summary = getattr(task, "verification_summary", {}) or {}
    if isinstance(summary, dict):
        tests_run = _completion_evidence_text(
            summary.get("tests_run", ""),
            limit=1000,
        )
        if tests_run:
            return tests_run
    evidence = getattr(task, "completion_evidence", {}) or {}
    if isinstance(evidence, dict):
        verification = evidence.get("verification", {}) or {}
        if isinstance(verification, dict):
            ver_summary = verification.get("summary", {}) or {}
            if isinstance(ver_summary, dict):
                tests_run = _completion_evidence_text(
                    ver_summary.get("tests_run", ""),
                    limit=1000,
                )
                if tests_run:
                    return tests_run
    return ""


def _covering_task_final_ship_evidence(task) -> tuple[dict, str]:
    """Return PR/SHA/test evidence only for final shipped covering tasks.

    Auto-resolving a routed proposal root is intentionally stricter than normal
    completion surfacing: the covering task must already be closed, have an
    origin-verified merge/PR SHA, and carry explicit tests/checks evidence.
    """
    if not task or not task_counts_as_done(task):
        return {}, "covering_task_not_done"
    evidence = getattr(task, "completion_evidence", {}) or {}
    if not isinstance(evidence, dict):
        return {}, "missing_completion_evidence"
    merge = evidence.get("merge", {}) or {}
    if not isinstance(merge, dict):
        return {}, "missing_merge_evidence"
    if not bool(merge.get("origin_verified")):
        return {}, "merge_not_origin_verified"
    pr_url = _completion_evidence_text(
        merge.get("pr_url")
        or (merge.get("pr", {}) or {}).get("url", ""),
        limit=500,
    )
    sha = _completion_evidence_text(
        merge.get("sha")
        or merge.get("origin_sha")
        or (merge.get("pr", {}) or {}).get("merge_commit_sha", ""),
        limit=120,
    )
    tests_run = _task_tests_run_completion_evidence(task)
    if not pr_url:
        return {}, "missing_pr_url"
    if not sha:
        return {}, "missing_merge_sha"
    if not tests_run:
        return {}, "missing_tests_run"

    origin_summary = _completion_evidence_text(
        merge.get("origin_summary", ""),
        limit=500,
    )
    shipped = {
        "pr_url": pr_url,
        "sha": sha,
        "tests_run": tests_run,
        "evidence": "final shipped covering task evidence",
        "notes": (
            "Auto-resolved product proposal root after covering task "
            "shipped with final PR/SHA/test evidence."
        ),
    }
    if origin_summary:
        shipped["notes"] += f" Origin verified: {origin_summary}."
    return shipped, ""


def _auto_resolve_product_proposal_roots_for_covering_task(
        state: MatrixState,
        covering_task,
) -> list[dict]:
    """Close routed product-proposal roots explicitly covered by a shipped task.

    This is deliberately a narrow follow-through path, not a generic cleanup
    sweep: it only considers roots in the same group with product labels, a
    different creator, an exact ``covers:<root>`` label on this covering task,
    the existing routed product-proposal authorization predicate, and final PR/SHA/tests
    evidence on the shipped covering task.
    """
    if not state or not covering_task:
        return []
    covering_task_id = str(getattr(covering_task, "id", "") or "").strip()
    covering_architect_id = str(
        getattr(covering_task, "created_by_architect_id", "") or ""
    ).strip()
    covering_group = str(getattr(covering_task, "group", "") or "").strip()
    if not covering_task_id or not covering_architect_id or not covering_group:
        return []

    final_evidence, _reason = _covering_task_final_ship_evidence(covering_task)
    if not final_evidence:
        return []

    try:
        from .mcp_tools_shared import (
            _PRODUCT_TASK_LABELS,
            _routed_product_root_coverage_authorization,
            _task_has_covers_label,
        )
    except Exception:
        log.exception("Failed to load routed product-proposal coverage authorization helpers")
        return []

    resolved = []
    for root in list(getattr(state, "board_tasks", {}).values()):
        root_id = str(getattr(root, "id", "") or "").strip()
        if not root_id or root_id == covering_task_id:
            continue
        if str(getattr(root, "group", "") or "").strip() != covering_group:
            continue
        if task_is_closed(root):
            continue
        root_creator = str(
            getattr(root, "created_by_architect_id", "") or ""
        ).strip()
        if not root_creator or root_creator == covering_architect_id:
            continue
        root_labels = {
            str(label or "").strip()
            for label in (getattr(root, "labels", []) or [])
        }
        # Auto-resolution is stricter than manual visibility: require both
        # product proposal labels so arbitrary cross-architect cards are not swept.
        if not set(_PRODUCT_TASK_LABELS).issubset(root_labels):
            continue
        # Avoid arbitrary/bulk cleanup: this shipped task must name this root.
        if not _task_has_covers_label(covering_task, root_id):
            continue

        existing_covered_by = {}
        existing_evidence = getattr(root, "completion_evidence", {}) or {}
        if isinstance(existing_evidence, dict):
            existing_covered_by = existing_evidence.get("covered_by", {}) or {}
        if isinstance(existing_covered_by, dict):
            existing_covering_id = str(
                existing_covered_by.get("task_id", "") or ""
            ).strip()
            if existing_covering_id and existing_covering_id != covering_task_id:
                continue

        authorization, auth_error = _routed_product_root_coverage_authorization(
            state,
            covering_architect_id,
            root,
            covering_task_id,
        )
        if auth_error or not authorization:
            continue
        # Route-only manual coverage remains possible, but automatic closure
        # requires an explicit covering-task label so the target is unambiguous.
        if "covering_task_label" not in str(
                authorization.get("source", "") or ""):
            continue
        authorization = dict(authorization)
        authorization["auto_resolved"] = True
        authorization["auto_resolve_source"] = "covering_task_final_ship_evidence"

        try:
            result = state.board_mark_task_covered(
                root_id,
                covering_task_id=covering_task_id,
                pr_url=final_evidence["pr_url"],
                sha=final_evidence["sha"],
                tests_run=final_evidence["tests_run"],
                evidence=final_evidence["evidence"],
                notes=final_evidence["notes"],
                actor_name="Torque",
                actor_id=covering_architect_id,
                actor_kind="system",
                authorization=authorization,
                move_to_done=True,
            )
        except ValueError:
            log.exception(
                "Failed to auto-resolve product proposal root %s covered by %s",
                root_id,
                covering_task_id,
            )
            continue
        resolved.append(result)
    return resolved


def _auto_resolve_product_proposal_roots_and_enqueue(
        state: MatrixState,
        covering_task,
        *,
        board_sync_manager=None,
) -> list[dict]:
    auto_resolved = _auto_resolve_product_proposal_roots_for_covering_task(
        state,
        covering_task,
    )
    if board_sync_manager:
        for resolved in auto_resolved:
            board_sync_manager.enqueue_for_local_change(
                resolved.get("task_id", ""),
                reason="auto_proposal_root_covered",
                fields=("completion_evidence", "messages", "lane"),
            )
    return auto_resolved


_PROPOSAL_ROOT_BACKLOG_HYGIENE_REASON = (
    "Backlog hygiene finalization for product proposal root with existing "
    "final covered_by PR/SHA/test evidence."
)


def _task_text_field(task, field: str, limit: int = 500) -> str:
    return _completion_evidence_text(
        getattr(task, field, "") if task is not None else "",
        limit=limit,
    )


def _proposal_root_backlog_hygiene_item(state: MatrixState, root) -> dict:
    """Classify one Backlog product proposal root for covered-by finalization."""
    root_id = _task_text_field(root, "id", 120)
    labels = [
        str(label or "").strip()
        for label in (getattr(root, "labels", []) or [])
        if str(label or "").strip()
    ]
    label_set = set(labels)
    item = {
        "task_id": root_id,
        "task": _task_text_field(root, "task", 500),
        "lane": _task_text_field(root, "lane", 80),
        "eligible": False,
        "reason": "",
        "labels": labels,
    }

    if not root_id:
        item["reason"] = "missing_task_id"
        return item
    if task_is_closed(root):
        item["reason"] = "already_closed"
        return item
    if item["lane"] != "Backlog":
        item["reason"] = "not_backlog"
        return item
    if str(getattr(root, "parent_task_id", "") or "").strip() \
            or int(getattr(root, "pipeline_depth", 0) or 0):
        item["reason"] = "not_root_task"
        return item

    try:
        from .mcp_tools_shared import _PRODUCT_TASK_LABELS
    except Exception:
        log.exception("Failed to load product proposal label constants")
        item["reason"] = "product_label_helper_unavailable"
        return item
    required_labels = set(_PRODUCT_TASK_LABELS)
    if not required_labels.issubset(label_set):
        item["reason"] = "not_product_proposal_root"
        item["missing_labels"] = sorted(required_labels - label_set)
        return item

    evidence = getattr(root, "completion_evidence", {}) or {}
    if not isinstance(evidence, dict):
        item["reason"] = "missing_completion_evidence"
        return item
    covered_by = evidence.get("covered_by", {}) or {}
    if not isinstance(covered_by, dict) or not covered_by:
        item["reason"] = "missing_covered_by_evidence"
        return item

    covering_task_id = _completion_evidence_text(
        covered_by.get("task_id", ""),
        limit=120,
    )
    item["covering_task_id"] = covering_task_id
    item["pr_url"] = _completion_evidence_text(
        covered_by.get("pr_url", ""),
        limit=500,
    )
    item["sha"] = _completion_evidence_text(
        covered_by.get("sha", ""),
        limit=120,
    )
    item["tests_run"] = _completion_evidence_text(
        covered_by.get("tests_run", ""),
        limit=1000,
    )
    item["moved_to_done"] = bool(covered_by.get("moved_to_done"))
    if item["moved_to_done"]:
        item["reason"] = "coverage_already_marked_moved_to_done"
        return item

    authorization = covered_by.get("authorization", {}) or {}
    if not isinstance(authorization, dict) or not authorization:
        item["reason"] = "missing_route_authorization"
        return item
    item["authorization_scope"] = _completion_evidence_text(
        authorization.get("scope", ""),
        limit=120,
    )
    item["authorization_source"] = _completion_evidence_text(
        authorization.get("source", ""),
        limit=200,
    )
    if item["authorization_scope"] != "routed_product_proposal_root":
        item["reason"] = "route_authorization_scope_mismatch"
        return item
    auth_covered_task_id = _completion_evidence_text(
        authorization.get("covered_task_id", ""),
        limit=120,
    )
    auth_covering_task_id = _completion_evidence_text(
        authorization.get("covering_task_id", ""),
        limit=120,
    )
    if auth_covered_task_id and auth_covered_task_id != root_id:
        item["reason"] = "route_authorization_task_mismatch"
        item["authorization_covered_task_id"] = auth_covered_task_id
        return item
    if auth_covering_task_id and covering_task_id \
            and auth_covering_task_id != covering_task_id:
        item["reason"] = "route_authorization_covering_task_mismatch"
        item["authorization_covering_task_id"] = auth_covering_task_id
        return item
    if not covering_task_id:
        item["reason"] = "ambiguous_covering_task"
        return item

    covering_task = state.board_tasks.get(covering_task_id)
    if not covering_task:
        item["reason"] = "covering_task_missing"
        return item
    item["covering_task_lane"] = _task_text_field(covering_task, "lane", 80)
    item["covering_task_title"] = _task_text_field(covering_task, "task", 500)
    if not task_counts_as_done(covering_task):
        item["reason"] = "pending_coverage"
        item["pending_detail"] = "covering_task_not_done"
        return item

    missing_final = [
        name for name in ("pr_url", "sha", "tests_run") if not item.get(name)
    ]
    if missing_final:
        item["reason"] = "missing_final_evidence"
        item["missing_final_evidence"] = missing_final
        return item

    item["eligible"] = True
    item["reason"] = "eligible_final_covered_by_evidence"
    return item


def _proposal_root_backlog_hygiene_inventory(
        state: MatrixState,
        *,
        group: str = "",
) -> list[dict]:
    """Inventory Backlog product proposal roots with eligibility reasons."""
    group = _completion_evidence_text(group, limit=200)
    items = []
    for root in sorted(
            list(getattr(state, "board_tasks", {}).values()),
            key=lambda task: (getattr(task, "created_at", "") or "",
                              getattr(task, "id", "") or "")):
        if group and str(getattr(root, "group", "") or "").strip() != group:
            continue
        labels = {
            str(label or "").strip()
            for label in (getattr(root, "labels", []) or [])
        }
        if not {"proposal-only", "product-proposal"}.issubset(labels):
            continue
        if str(getattr(root, "lane", "") or "").strip() != "Backlog":
            continue
        items.append(_proposal_root_backlog_hygiene_item(state, root))
    return items


def _proposal_root_backlog_hygiene_authorized_for_architect(
        state: MatrixState,
        item: dict,
        architect_id: str,
        group: str,
) -> tuple[bool, str]:
    """Return whether an architect may finalize this inventory item.

    Backlog hygiene is intentionally narrower than generic task ownership:
    a caller may only finalize an eligible routed proposal root in their own group
    when the durable ``covered_by`` record points at a covering task that was
    created by that same Architect caller.
    """
    architect_id = _completion_evidence_text(architect_id, limit=120)
    group = _completion_evidence_text(group, limit=200)
    if not architect_id:
        return False, "missing_architect_id"
    if not group:
        return False, "missing_architect_group"
    task_id = _completion_evidence_text(item.get("task_id", ""), limit=120)
    root = state.board_tasks.get(task_id)
    if not root or str(getattr(root, "group", "") or "").strip() != group:
        return False, "task_not_in_architect_group"
    root_architect_id = str(
        getattr(root, "created_by_architect_id", "") or ""
    ).strip()
    if root_architect_id == architect_id:
        return False, "not_cross_architect_proposal_root"
    if not item.get("eligible"):
        return False, str(item.get("reason", "") or "not_eligible")

    covering_task_id = _completion_evidence_text(
        item.get("covering_task_id", ""),
        limit=120,
    )
    covering_task = state.board_tasks.get(covering_task_id)
    if not covering_task:
        return False, "covering_task_missing"
    if str(getattr(covering_task, "group", "") or "").strip() != group:
        return False, "covering_task_not_in_architect_group"
    covering_architect_id = str(
        getattr(covering_task, "created_by_architect_id", "") or ""
    ).strip()
    if covering_architect_id != architect_id:
        return False, "covering_task_not_created_by_architect"

    return True, ""


def _finalize_already_covered_proposal_roots(
        state: MatrixState,
        *,
        apply: bool = False,
        task_ids: list[str] | None = None,
        limit: int = 0,
        board_sync_manager=None,
        architect_id: str = "",
        group: str = "",
) -> dict:
    """Dry-run or apply backlog hygiene for already-covered proposal roots."""
    requested = {
        state.resolve_task_alias(str(task_id or "").strip())
        for task_id in (task_ids or [])
        if str(task_id or "").strip()
    }
    inventory = _proposal_root_backlog_hygiene_inventory(state, group=group)
    if requested:
        inventory = [
            item for item in inventory
            if item.get("task_id") in requested
        ]
    if architect_id:
        scoped_inventory = []
        for item in inventory:
            authorized, reason = (
                _proposal_root_backlog_hygiene_authorized_for_architect(
                    state,
                    item,
                    architect_id,
                    group,
                )
            )
            scoped_item = dict(item)
            scoped_item["authorized_for_architect"] = bool(authorized)
            if not authorized:
                scoped_item["eligible"] = False
                scoped_item["reason"] = reason
            scoped_inventory.append(scoped_item)
        inventory = scoped_inventory
    eligible = [item for item in inventory if item.get("eligible")]
    if limit and limit > 0:
        eligible_to_apply = eligible[:limit]
    else:
        eligible_to_apply = eligible

    finalized = []
    errors = []
    if apply:
        for item in eligible_to_apply:
            task_id = item.get("task_id", "")
            covering = state.board_tasks.get(item.get("covering_task_id", ""))
            actor_id = _task_text_field(covering, "created_by_architect_id", 120) \
                or _completion_evidence_text(
                    ((state.board_tasks.get(task_id).completion_evidence or {})
                     .get("covered_by", {}) or {}).get("recorded_by_id", ""),
                    limit=120,
                )
            try:
                result = state.board_finalize_existing_task_coverage(
                    task_id,
                    actor_name="Torque",
                    actor_id=actor_id,
                    actor_kind="system",
                    reason=_PROPOSAL_ROOT_BACKLOG_HYGIENE_REASON,
                )
            except ValueError as exc:
                errors.append({
                    "task_id": task_id,
                    "reason": str(exc),
                })
                continue
            finalized.append(result)
            if board_sync_manager:
                board_sync_manager.enqueue_for_local_change(
                    task_id,
                    reason="proposal_root_backlog_hygiene_finalized",
                    fields=("completion_evidence", "messages", "lane"),
                )

    return {
        "type": "proposal_root_backlog_hygiene",
        "scope": "architect" if architect_id else "internal",
        "architect_id": _completion_evidence_text(architect_id, limit=120),
        "group": _completion_evidence_text(group, limit=200),
        "apply": bool(apply),
        "eligible_count": len(eligible),
        "ineligible_count": len(inventory) - len(eligible),
        "inventory_count": len(inventory),
        "limit": int(limit or 0),
        "applied_count": len(finalized),
        "finalized": finalized,
        "errors": errors,
        "items": inventory,
    }


def _record_task_completion_evidence_snapshot(
        state: MatrixState,
        task,
        *,
        cell=None,
        action: str = "",
        message: str = "",
        actor_name: str = "",
        timestamp: str = "",
        board_sync_manager=None,
) -> bool:
    """Snapshot verification/artifact evidence when a task is completed.

    This intentionally never gates completion: it records evidence already
    supplied through ``torque_verify`` / task artifacts so a Done claim has a
    durable breadcrumb trail.
    """
    if not state or not task:
        return False

    verification = _task_verification_evidence(task)
    artifacts = _task_artifact_evidence(task)
    sources = []
    update = {
        "updated_at": timestamp or datetime.now(timezone.utc).isoformat(),
        "updated_by": actor_name
        or _completion_evidence_text(getattr(cell, "name", ""))
        or "torque",
    }
    if action:
        update["completion"] = {
            "action": _completion_evidence_text(action, limit=80),
            "message": _completion_evidence_text(message, limit=2000),
            "agent_id": _completion_evidence_text(
                getattr(cell, "id", ""), limit=80),
            "agent_name": _completion_evidence_text(
                getattr(cell, "name", ""), limit=160),
            "recorded_at": update["updated_at"],
        }
    if verification:
        update["verification"] = verification
        sources.append("verification")
    if artifacts:
        update["artifacts"] = artifacts
        sources.append("artifacts")
    if not sources:
        return False
    update["sources"] = sources
    task.completion_evidence = _merge_completion_evidence(
        getattr(task, "completion_evidence", {}) or {},
        update,
    )
    _save_completion_evidence_task(state, task)
    _auto_resolve_product_proposal_roots_and_enqueue(
        state,
        task,
        board_sync_manager=board_sync_manager,
    )
    return True


def _origin_verification_evidence(
        *,
        merge_sha: str,
        remote: str = "",
        base_branch: str = "",
        post_merge_sync: dict | None = None,
        authoritative_guard: dict | None = None,
) -> dict:
    merge_sha = str(merge_sha or "").strip()
    remote = str(remote or "").strip()
    base_branch = str(base_branch or "").strip()
    source = ""
    matched_sha = ""
    result = None

    guard = authoritative_guard if isinstance(authoritative_guard, dict) else {}
    if guard.get("ok"):
        match = guard.get("base_match")
        if isinstance(match, dict):
            matched_sha = str(match.get("sha") or "").strip()
            source = str(match.get("source") or "authoritative_guard").strip()
            result = match.get("result") if isinstance(
                match.get("result"), dict) else None
            remote = str(match.get("remote") or remote or "").strip()
            base_branch = str(
                match.get("base_branch")
                or match.get("ref")
                or base_branch
                or ""
            ).strip()

    if not matched_sha and isinstance(post_merge_sync, dict):
        match = _base_match_from_result(post_merge_sync, merge_sha)
        if match:
            matched_sha = str(match.get("sha") or "").strip()
            source = str(match.get("source") or "remote_base_sync").strip()
            result = post_merge_sync
        else:
            matched_sha = str(
                post_merge_sync.get("remote_sha")
                or post_merge_sync.get("base_sha")
                or ""
            ).strip()
            result = post_merge_sync
        remote = str(post_merge_sync.get("remote") or remote or "").strip()
        base_branch = str(
            post_merge_sync.get("base_branch") or base_branch or ""
        ).strip()
        if not source:
            source = str(post_merge_sync.get("phase") or "remote_base_sync")

    verified = bool(merge_sha and matched_sha and _sha_equal(matched_sha, merge_sha))
    origin_ref = ""
    if remote and base_branch:
        origin_ref = f"{remote}/{base_branch}"
    elif base_branch:
        origin_ref = base_branch
    evidence = {
        "verified": verified,
        "sha": matched_sha,
        "expected_sha": merge_sha,
        "ref": origin_ref,
        "source": source,
    }
    if result:
        for key in ("phase", "remote", "base_branch", "base_sha",
                    "remote_sha", "synced"):
            if key in result:
                evidence[key] = result[key]
    if verified and origin_ref and merge_sha:
        evidence["summary"] = f"{origin_ref} == {merge_sha}"
    return {k: v for k, v in evidence.items() if v not in ("", None)}


def _merge_evidence_matches_boundary(boundary: dict, *,
                                     repo_root: str,
                                     branch: str,
                                     merge_sha: str) -> bool:
    if not isinstance(boundary, dict):
        return False
    if str(boundary.get("status") or "").strip() != "merged":
        return False
    if merge_sha and str(boundary.get("merge_commit_sha") or "").strip() != merge_sha:
        return False
    boundary_branch = str(boundary.get("branch") or "").strip()
    if branch and boundary_branch and boundary_branch != branch:
        return False
    boundary_repo = str(boundary.get("repo_root") or "").strip()
    if repo_root and boundary_repo and boundary_repo != repo_root:
        return False
    return True


def _record_merge_completion_evidence(
        state: MatrixState,
        *,
        result: dict,
        cell=None,
        repo_root: str = "",
        branch: str = "",
        base_branch: str = "",
        remote: str = "",
        origin_verification: dict | None = None,
        board_sync_manager=None,
) -> list[str]:
    if not state or not isinstance(result, dict) or not result.get("ok"):
        return []
    merge_sha = str(
        result.get("sha") or result.get("merge_commit_sha") or ""
    ).strip()
    if not merge_sha or bool(result.get("pending")):
        return []

    branch = str(branch or result.get("branch") or "").strip()
    base_branch = str(base_branch or result.get("base_branch") or "").strip()
    repo_root = str(repo_root or getattr(cell, "worktree_repo_root", "")
                    or getattr(cell, "git_root", "") or "").strip()
    remote = str(remote or "").strip()
    origin = origin_verification if isinstance(origin_verification, dict) else {}
    merge = {
        "sha": merge_sha,
        "mode": _completion_evidence_text(result.get("mode", "direct"), limit=80),
        "branch": _completion_evidence_text(branch, limit=240),
        "base_branch": _completion_evidence_text(base_branch, limit=120),
        "remote": _completion_evidence_text(remote, limit=120),
        "pr_url": _completion_evidence_text(
            result.get("pr_url") or result.get("url") or "", limit=500),
        "origin_verified": bool(origin.get("verified")),
    }
    if origin:
        merge["origin"] = origin
        if origin.get("ref"):
            merge["origin_ref"] = origin["ref"]
        if origin.get("sha"):
            merge["origin_sha"] = origin["sha"]
        if origin.get("summary"):
            merge["origin_summary"] = origin["summary"]
    if isinstance(result.get("pr"), dict):
        pr = result["pr"]
        for key in ("number", "url", "state", "merge_commit_sha"):
            if pr.get(key) not in (None, ""):
                merge.setdefault("pr", {})[key] = pr[key]

    timestamp = datetime.now(timezone.utc).isoformat()
    actor_name = _completion_evidence_text(getattr(cell, "name", "")) \
        or _completion_evidence_text(result.get("agent_name", "")) \
        or "torque"
    update = {
        "sources": ["merge"],
        "merge": merge,
        "updated_at": timestamp,
        "updated_by": actor_name,
    }
    updated_ids = []
    for task in list(state.board_tasks.values()):
        boundary = getattr(task, "worktree_boundary", {}) or {}
        if not _merge_evidence_matches_boundary(
                boundary,
                repo_root=repo_root,
                branch=branch,
                merge_sha=merge_sha,
        ):
            continue
        task.completion_evidence = _merge_completion_evidence(
            getattr(task, "completion_evidence", {}) or {},
            update,
        )
        # New opt-in policy roots require an explicit machine tree-parity
        # object.  Do not manufacture it from merge prose or SHA coincidence.
        # Older merge evidence remains advisory and legacy-compatible.
        finalization_boundary = getattr(task, "finalization_boundary", {}) or {}
        parity = result.get("tree_equality")
        if (
                getattr(task, "finalization_mode", "legacy") == "merge"
                and isinstance(finalization_boundary, dict)
                and isinstance(parity, dict)
                and parity.get("equal") is True
                and str(parity.get("reviewed_tree") or "").strip()
                and str(parity.get("merged_tree") or "").strip()):
            policy_mode = str(result.get("mode") or "").strip().lower()
            if policy_mode in {"pull_request", "pull-request"}:
                policy_mode = "pr"
            if policy_mode not in {"pr", "direct"}:
                policy_mode = "direct"
            state.record_merge_finalization(
                task.id,
                mode=policy_mode,
                reference=str(result.get("pr_url") or result.get("url")
                              or ("merge:" + merge_sha)),
                reviewed_head_sha=str(
                    result.get("reviewed_head_sha")
                    or parity.get("reviewed_head_sha") or ""),
                merged_sha=merge_sha,
                origin_verified=bool(origin.get("verified")),
                reviewed_tree=str(parity.get("reviewed_tree") or ""),
                merged_tree=str(parity.get("merged_tree") or ""),
                equal=True,
            )
        _save_completion_evidence_task(state, task)
        _auto_resolve_product_proposal_roots_and_enqueue(
            state,
            task,
            board_sync_manager=board_sync_manager,
        )
        updated_ids.append(task.id)
    return updated_ids
