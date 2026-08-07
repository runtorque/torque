"""Durable verification-run read models and SHA currentness derivation."""

from __future__ import annotations


def _text(value, *, limit=2000) -> str:
    text = str(value or "").strip()
    if limit > 0 and len(text) > limit:
        return text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def _verification_reference_shas(task) -> tuple[list[str], list[str]]:
    """Return reviewed and merged SHAs used to derive run currentness."""
    if not task:
        return [], []
    reviewed_candidates = []
    merge_candidates = []
    boundary = getattr(task, "worktree_boundary", {}) or {}
    if isinstance(boundary, dict):
        reviewed_candidates.append(boundary.get("commit_sha", ""))
        merge_candidates.append(boundary.get("merge_commit_sha", ""))
        boundary_pr = boundary.get("pr", {}) or {}
        if isinstance(boundary_pr, dict):
            merge_candidates.append(boundary_pr.get("merge_commit_sha", ""))
    finalization_boundary = getattr(task, "finalization_boundary", {}) or {}
    if isinstance(finalization_boundary, dict):
        reviewed_candidates.extend((
            finalization_boundary.get("head_sha", ""),
            finalization_boundary.get("reviewed_head_sha", ""),
        ))
    completion = getattr(task, "completion_evidence", {}) or {}
    if isinstance(completion, dict):
        merge = completion.get("merge", {}) or {}
        if isinstance(merge, dict):
            merge_candidates.extend((
                merge.get("sha", ""),
                merge.get("origin_sha", ""),
                merge.get("merge_commit_sha", ""),
            ))
            merge_pr = merge.get("pr", {}) or {}
            if isinstance(merge_pr, dict):
                merge_candidates.append(merge_pr.get("merge_commit_sha", ""))

    def _normalize(candidates):
        references = []
        for value in candidates:
            sha = _text(value, limit=160)
            if sha and sha not in references:
                references.append(sha)
        return references

    return _normalize(reviewed_candidates), _normalize(merge_candidates)


def _verification_run_currentness(task, tested_sha: str) -> str:
    """Derive a run's currentness without persisting a mutable verdict."""
    tested_sha = _text(tested_sha, limit=160)
    if not tested_sha:
        return "unknown"
    reviewed_references, merge_references = _verification_reference_shas(task)
    if not reviewed_references and not merge_references:
        return "unknown"
    if tested_sha in (*reviewed_references, *merge_references):
        return "current"
    # A reviewed boundary gives an authoritative candidate identity. A
    # different tested SHA is therefore superseded. A squash merge alone does
    # not: its commit SHA normally differs from the tested candidate SHA.
    return "superseded" if reviewed_references else "unknown"


def _verification_summary_evidence(summary) -> dict:
    if not isinstance(summary, dict):
        return {}
    normalized = {}
    for key in (
        "tests_run",
        "human_validation_pending",
        "isolated_rerun_evidence",
        "test_outcome",
        "reviewer_acceptance",
        "tested_sha",
    ):
        value = _text(summary.get(key, ""))
        if value:
            normalized[key] = value
    for key in (
        "manual_smoke_done",
        "deploy_needed",
        "deploy_attempted",
        "full_suite_attempted",
        "unrelated_flake_accepted",
        "live_smoke_pending",
    ):
        if key in summary:
            normalized[key] = bool(summary.get(key))
    return normalized


def task_verification_evidence(task, *, include_currentness=True) -> dict:
    """Build a task verification view; currentness is never stored."""
    if not task:
        return {}
    summary = getattr(task, "verification_summary", {}) or {}
    if not isinstance(summary, dict):
        summary = {}
    evidence = {}
    state_value = _text(getattr(task, "verification_state", ""))
    mode_value = _text(getattr(task, "verification_mode", ""))
    notes_value = _text(getattr(task, "verification_notes", ""))
    if state_value:
        evidence["state"] = state_value
    if mode_value:
        evidence["mode"] = mode_value
    normalized_summary = _verification_summary_evidence(summary)
    if include_currentness and normalized_summary:
        normalized_summary["currentness"] = _verification_run_currentness(
            task,
            normalized_summary.get("tested_sha", ""),
        )
    if normalized_summary:
        evidence["summary"] = normalized_summary
    normalized_runs = []
    for run in summary.get("runs", []) or []:
        if not isinstance(run, dict):
            continue
        report = run.get("report", {}) or {}
        if not isinstance(report, dict):
            continue
        run_evidence = {}
        recorded_at = _text(run.get("recorded_at", ""))
        recorded_by = _text(run.get("recorded_by", ""))
        if recorded_at:
            run_evidence["recorded_at"] = recorded_at
        if recorded_by:
            run_evidence["recorded_by"] = recorded_by
        normalized_report = {}
        for key in ("mode", "state", "notes"):
            value = _text(report.get(key, ""))
            if value:
                normalized_report[key] = value
        report_summary = _verification_summary_evidence(
            report.get("summary", {})
        )
        if report_summary:
            normalized_report["summary"] = report_summary
        if normalized_report:
            run_evidence["report"] = normalized_report
        if include_currentness:
            run_evidence["currentness"] = _verification_run_currentness(
                task,
                report_summary.get("tested_sha", ""),
            )
        if run_evidence:
            normalized_runs.append(run_evidence)
    if normalized_runs:
        evidence["runs"] = normalized_runs
    if notes_value:
        evidence["notes"] = notes_value
    updated_at = _text(getattr(task, "verification_updated_at", ""))
    updated_by = _text(getattr(task, "verification_updated_by", ""))
    if updated_at:
        evidence["updated_at"] = updated_at
    if updated_by:
        evidence["updated_by"] = updated_by
    return evidence
