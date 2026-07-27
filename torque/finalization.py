"""Canonical, machine-readable finalization policy for pipeline roots.

This module deliberately does not interpret task prose, labels, or reviewer
summaries.  New finalization contracts are opt-in and legacy cards retain their
historic completion semantics.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

FINALIZATION_MODES = frozenset({"legacy", "merge", "review_only"})
TERMINAL_DISPOSITIONS = frozenset({"covered", "superseded", "cancelled", "stale_boundary"})
AUDIT_LIMIT = 40


def _text(value: Any, limit: int = 240) -> str:
    # Keep audit / UI values reader-safe and bounded.  Never echo arbitrary
    # evidence blobs or review prose through this contract.
    return str(value or "").replace("\x00", "").strip()[:limit]


def normalize_mode(value: Any) -> str:
    value = _text(value, 32).lower().replace("-", "_")
    return value if value in FINALIZATION_MODES else "legacy"


def normalize_required_review_gates(value: Any) -> list[dict]:
    """Normalize only explicit gate declarations, retaining declaration order."""
    if not isinstance(value, list):
        return []
    result: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        gate_id = _text(item.get("id"), 80)
        role = _text(item.get("role"), 80)
        if not gate_id or gate_id in seen:
            continue
        seen.add(gate_id)
        out = {"id": gate_id, "role": role}
        # A review task relationship is explicit; roles must never be guessed.
        review_task_id = _text(item.get("review_task_id"), 100)
        if review_task_id:
            out["review_task_id"] = review_task_id
        result.append(out)
    return result


def normalize_boundary(value: Any, mode: str = "legacy") -> dict:
    if not isinstance(value, dict):
        return {}
    # Preserve structured evidence but trim its leaf text so corrupt rows cannot
    # turn snapshot data into an unbounded payload.
    out: dict = {}
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 80:
            continue
        if isinstance(item, str):
            out[key] = _text(item, 500)
        elif isinstance(item, bool) or isinstance(item, (int, float)):
            out[key] = item
        elif isinstance(item, dict):
            nested = {}
            for nested_key, nested_value in item.items():
                if not isinstance(nested_key, str) or len(nested_key) > 80:
                    continue
                if isinstance(nested_value, (str, int, float, bool)) or nested_value is None:
                    nested[nested_key] = _text(nested_value, 500) if isinstance(nested_value, str) else nested_value
                elif isinstance(nested_value, dict):
                    # Finalization's tree-equality proof is intentionally a
                    # small second-level structured fact.
                    nested[nested_key] = {
                        str(k)[:80]: (_text(v, 500) if isinstance(v, str) else v)
                        for k, v in nested_value.items()
                        if isinstance(k, str) and (isinstance(v, (str, int, float, bool)) or v is None)
                    }
            out[key] = nested
    return out


def normalize_audit(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value[-AUDIT_LIMIT:]:
        if not isinstance(item, dict):
            continue
        codes = item.get("missing_gates", [])
        if not isinstance(codes, list):
            codes = []
        out.append({
            "at": _text(item.get("at"), 64),
            "caller": _text(item.get("caller"), 100),
            "mode": normalize_mode(item.get("mode")),
            "outcome": "success" if item.get("outcome") == "success" else "blocked",
            "boundary": _text(item.get("boundary"), 160),
            "missing_gates": [_text(code, 120) for code in codes[:20] if _text(code, 120)],
        })
    return out


def boundary_key(boundary: dict, mode: str) -> str:
    if not isinstance(boundary, dict):
        return ""
    if mode == "merge":
        return _text(boundary.get("head_sha") or boundary.get("commit_sha"), 160)
    if mode == "review_only":
        return "|".join((_text(boundary.get("artifact_digest"), 160),
                         _text(boundary.get("artifact_version"), 160),
                         _text(boundary.get("source_identity"), 160)))
    return ""


def _merge_boundary_errors(boundary: dict, worktree_boundary: dict) -> list[str]:
    missing = []
    for key in ("repository", "base_sha", "head_sha"):
        # repo_root is accepted as repository identity for the pre-existing
        # worktree boundary shape.
        if not _text(boundary.get(key) or (boundary.get("repo_root") if key == "repository" else "")):
            missing.append(f"boundary_missing_{key}")
    clean = boundary.get("clean_evidence")
    diff = boundary.get("diff_evidence")
    if not isinstance(clean, dict) or clean.get("clean") is not True:
        missing.append("boundary_missing_clean_evidence")
    if not isinstance(diff, dict):
        missing.append("boundary_missing_diff_evidence")
    current_head = _text((worktree_boundary or {}).get("commit_sha"))
    expected_head = _text(boundary.get("head_sha"))
    if current_head and expected_head and current_head != expected_head:
        missing.append("boundary_advanced")
    return missing


def _review_only_boundary_errors(boundary: dict) -> list[str]:
    missing = []
    for key in ("artifact_digest", "artifact_version", "source_identity"):
        if not _text(boundary.get(key)):
            missing.append(f"boundary_missing_{key}")
    if boundary.get("immutable") is not True:
        missing.append("boundary_not_immutable")
    return missing


def _descendants(state, root_id: str):
    # Use structural parent links only.  Task names/labels are never inputs.
    tasks = getattr(state, "board_tasks", {}) or {}
    result = []
    queue = [root_id]
    seen = {root_id}
    while queue:
        parent = queue.pop(0)
        for task in tasks.values():
            if _text(getattr(task, "parent_task_id", ""), 100) != parent:
                continue
            if task.id in seen:
                continue
            seen.add(task.id)
            result.append(task)
            queue.append(task.id)
    return result


def _is_closed(task: Any) -> bool:
    return _text(getattr(task, "lane", ""), 40) in {"Done", "Archived"}


def _disposition(task: Any) -> str:
    evidence = getattr(task, "completion_evidence", {}) or {}
    if isinstance(evidence, dict):
        marker = evidence.get("finalization_disposition", {})
        if isinstance(marker, dict):
            return _text(marker.get("state"), 40).lower()
    return ""


def _review_record(task: Any) -> dict:
    evidence = getattr(task, "completion_evidence", {}) or {}
    if not isinstance(evidence, dict):
        return {}
    review = evidence.get("finalization_review", {})
    return review if isinstance(review, dict) else {}


def _gate_review(state, root, gate: dict):
    gate_id = gate["id"]
    review_task_id = _text(gate.get("review_task_id"), 100)
    task = (getattr(state, "board_tasks", {}) or {}).get(review_task_id) if review_task_id else None
    if not task:
        return None, f"review_gate_{gate_id}_missing"
    record = _review_record(task)
    if not record or record.get("executed") is not True:
        return task, f"review_gate_{gate_id}_not_executed"
    if _text(record.get("gate_id"), 80) != gate_id:
        return task, f"review_gate_{gate_id}_identity_mismatch"
    if _text(record.get("verdict"), 40).lower() != "ship":
        return task, f"review_gate_{gate_id}_not_ship"
    if record.get("has_blocking_issues") is not False:
        return task, f"review_gate_{gate_id}_blockers"
    if record.get("required_follow_up_resolved") is not True:
        return task, f"review_gate_{gate_id}_follow_up"
    expected = boundary_key(getattr(root, "finalization_boundary", {}) or {}, normalize_mode(getattr(root, "finalization_mode", "legacy")))
    if not expected or _text(record.get("boundary"), 500) != expected:
        return task, f"review_gate_{gate_id}_boundary_mismatch"
    return task, ""


def _merge_evidence_errors(boundary: dict) -> list[str]:
    evidence = boundary.get("finalization")
    if not isinstance(evidence, dict):
        return ["merge_not_finalized"]
    codes = []
    if _text(evidence.get("mode")) not in {"direct", "pr"}:
        codes.append("merge_missing_mode")
    if not _text(evidence.get("reference") or evidence.get("pr_url")):
        codes.append("merge_missing_reference")
    if _text(evidence.get("reviewed_head_sha")) != _text(boundary.get("head_sha")):
        codes.append("merge_reviewed_head_mismatch")
    if not _text(evidence.get("merged_sha")):
        codes.append("merge_missing_merged_sha")
    if evidence.get("origin_verified") is not True:
        codes.append("merge_origin_not_verified")
    tree = evidence.get("tree_equality")
    if not isinstance(tree, dict) or tree.get("equal") is not True:
        codes.append("merge_tree_not_equal")
    elif (not _text(tree.get("reviewed_tree")) or not _text(tree.get("merged_tree"))
          or _text(tree.get("reviewed_tree")) != _text(tree.get("merged_tree"))):
        codes.append("merge_tree_not_equal")
    return codes


def evaluate_finalization(state, task_or_id) -> dict:
    """Evaluate a root's Done eligibility without changing it.

    The result is intentionally compact, deterministic, and stable for API,
    CLI, board and MCP callers.  ``missing_gates`` is ordered by policy stage.
    """
    tasks = getattr(state, "board_tasks", {}) or {}
    task = tasks.get(task_or_id) if isinstance(task_or_id, str) else task_or_id
    if not task:
        return {"eligible": False, "mode": "legacy", "stage": "unknown", "boundary": "", "missing_gates": ["task_missing"], "explanations": ["Task is unavailable."]}
    root_id = _text(getattr(task, "pipeline_root_id", ""), 100) or _text(getattr(task, "id", ""), 100)
    root = tasks.get(root_id, task)
    mode = normalize_mode(getattr(root, "finalization_mode", "legacy"))
    if mode == "legacy":
        return {"eligible": True, "mode": mode, "stage": "legacy", "boundary": "", "missing_gates": [], "explanations": []}

    boundary = getattr(root, "finalization_boundary", {}) or {}
    if not isinstance(boundary, dict):
        boundary = {}
    boundary = normalize_boundary(boundary, mode)
    missing = _merge_boundary_errors(boundary, getattr(root, "worktree_boundary", {}) or {}) if mode == "merge" else _review_only_boundary_errors(boundary)
    gates = normalize_required_review_gates(getattr(root, "required_review_gates", []))
    if not gates:
        missing.append("required_review_gates_missing")
    else:
        for gate in gates:
            _review, code = _gate_review(state, root, gate)
            if code:
                missing.append(code)
    for child in _descendants(state, root.id):
        if _is_closed(child) or _disposition(child) in TERMINAL_DISPOSITIONS:
            continue
        missing.append(f"relevant_descendant_open:{_text(child.id, 100)}")

    boundary_incomplete = any(code.startswith("boundary_") for code in missing)
    review_incomplete = any(code.startswith(("review_gate_", "required_review")) for code in missing)
    descendants_open = any(code.startswith("relevant_descendant_open") for code in missing)
    reviews_ready = not (boundary_incomplete or review_incomplete or descendants_open)
    if mode == "merge":
        if reviews_ready:
            missing.extend(_merge_evidence_errors(boundary))
        if boundary_incomplete:
            stage = "implementing"
        elif review_incomplete:
            stage = "fixing" if any("blockers" in code or "follow_up" in code for code in missing) else "reviewing"
        elif descendants_open:
            stage = "fixing"
        else:
            # Merge evidence is purposely not a Done condition until guarded
            # finalization records it; this is the operator-facing handoff.
            stage = "ready_to_merge"
    else:
        if not missing:
            stage = "ready_to_finalize"
        elif boundary_incomplete:
            stage = "implementing"
        elif descendants_open or any("blockers" in code or "follow_up" in code for code in missing):
            stage = "fixing"
        else:
            stage = "reviewing"
    # preserve order but suppress accidental duplicate errors from malformed data.
    missing = list(dict.fromkeys(missing))
    explanations = [_explanation(code) for code in missing[:12]]
    return {"eligible": not missing, "mode": mode, "stage": stage,
            "boundary": boundary_key(boundary, mode), "missing_gates": missing,
            "explanations": explanations}


def _explanation(code: str) -> str:
    if code == "boundary_advanced":
        return "The implementation boundary advanced; reviews must cover the new boundary."
    if code == "merge_not_finalized":
        return "Guarded merge finalization has not completed."
    if code == "merge_origin_not_verified":
        return "The merged result has not been verified against origin."
    if code == "merge_tree_not_equal":
        return "Reviewed and merged trees do not have explicit equality evidence."
    if code.startswith("review_gate_"):
        return "A required review gate lacks an executed Ship verdict for this exact boundary."
    if code.startswith("relevant_descendant_open"):
        return "A relevant follow-up task is still open."
    if code.startswith("boundary_"):
        return "The immutable finalization boundary is incomplete or invalid."
    return "A required finalization gate is not satisfied."


def status_projection(result: dict) -> dict:
    stage = result.get("stage", "")
    label = {"legacy": "", "implementing": "Implementing", "reviewing": "Reviewing",
             "fixing": "Fixing blockers", "ready_to_merge": "Ready to merge",
             "ready_to_finalize": "Ready to finalize"}.get(stage, "Reviewing")
    return {"mode": result.get("mode", "legacy"), "stage": stage,
            "label": label, "eligible": bool(result.get("eligible")),
            "missing_gates": list(result.get("missing_gates", []))[:8]}


def audit_entry(result: dict, *, caller: str, outcome: str) -> dict:
    return {"at": datetime.now(timezone.utc).isoformat(), "caller": _text(caller, 100),
            "mode": result.get("mode", "legacy"),
            "outcome": "success" if outcome == "success" else "blocked",
            "boundary": _text(result.get("boundary"), 160),
            "missing_gates": list(result.get("missing_gates", []))[:20]}
