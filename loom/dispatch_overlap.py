"""Deterministic dispatch-overlap heuristics for Loom."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import time
from typing import Any

from .playbooks import _normalize_task_text, _stable_labels, _topic_key

OVERLAP_NONE = ""
OVERLAP_NOTICE = "notice"
OVERLAP_WARNING = "warning"
OVERLAP_CONFLICT = "conflict"

OVERLAP_SEVERITY = {
    OVERLAP_NONE: 0,
    OVERLAP_NOTICE: 1,
    OVERLAP_WARNING: 2,
    OVERLAP_CONFLICT: 3,
}

_HISTORY_WINDOW_SECS = 7 * 24 * 60 * 60
_MAX_FINDINGS = 5


@dataclass(frozen=True)
class DispatchOverlapSnapshot:
    level: str
    findings: list[dict]


def compute_dispatch_overlap(tasks_by_id: dict[str, Any],
                             agents_by_id: dict[str, Any],
                             db=None) -> tuple[dict[str, dict], dict[str, dict]]:
    """Compute advisory overlap snapshots for open tasks."""
    snapshots: dict[str, dict] = {}
    by_group: defaultdict[str, list] = defaultdict(list)

    open_tasks = [
        task for task in tasks_by_id.values()
        if getattr(task, "lane", "") != "Done"
    ]
    contexts = {
        task.id: _build_task_context(task, tasks_by_id, agents_by_id)
        for task in open_tasks
    }
    history = _load_recent_history(tasks_by_id, db)

    for task in open_tasks:
        ctx = contexts[task.id]
        findings: list[dict] = []
        for other in open_tasks:
            if other.id == task.id:
                continue
            findings.extend(_pair_findings(
                ctx,
                contexts[other.id],
                sequential=_is_sequential_same_agent(ctx, contexts[other.id]),
            ))
        findings.extend(_history_findings(ctx, history.get(task.group, [])))
        snapshot = _finalize_snapshot(findings)
        if snapshot.level:
            payload = {
                "level": snapshot.level,
                "summary": _snapshot_summary(snapshot),
                "findings": snapshot.findings,
            }
            snapshots[task.id] = payload
            by_group[task.group].append({
                "task_id": task.id,
                "title": getattr(task, "task", ""),
                "level": snapshot.level,
                "summary": payload["summary"],
            })

    group_summaries = {}
    for group, items in by_group.items():
        counts = {
            OVERLAP_NOTICE: 0,
            OVERLAP_WARNING: 0,
            OVERLAP_CONFLICT: 0,
        }
        for item in items:
            counts[item["level"]] += 1
        items.sort(key=lambda item: (
            -OVERLAP_SEVERITY.get(item["level"], 0),
            item["title"].lower(),
            item["task_id"],
        ))
        group_summaries[group] = {
            "counts": counts,
            "total": sum(counts.values()),
            "items": items[:_MAX_FINDINGS],
            "truncated": len(items) > _MAX_FINDINGS,
        }
    return snapshots, group_summaries


def assess_dispatch_overlap(tasks_by_id: dict[str, Any],
                            agents_by_id: dict[str, Any],
                            task: Any,
                            *,
                            db=None,
                            target_agent_id: str = "",
                            create_agent: bool = False,
                            handoff_from: str = "") -> dict:
    """Assess overlap risk for a dispatch request before it runs."""
    ctx = _build_task_context(
        task, tasks_by_id, agents_by_id, target_agent_id=target_agent_id)
    findings: list[dict] = []

    for other in tasks_by_id.values():
        if getattr(other, "id", "") == getattr(task, "id", ""):
            continue
        if getattr(other, "group", "") != getattr(task, "group", ""):
            continue
        if getattr(other, "lane", "") == "Done":
            continue
        other_ctx = _build_task_context(other, tasks_by_id, agents_by_id)
        findings.extend(_pair_findings(
            ctx,
            other_ctx,
            sequential=(
                _is_dispatch_sequential(ctx, other_ctx, create_agent)
                or (handoff_from and other_ctx["agent_id"] == handoff_from)
            ),
            pending_dispatch=True,
        ))

    findings.extend(_history_findings(ctx, _load_recent_history(
        tasks_by_id, db).get(getattr(task, "group", ""), [])))
    snapshot = _finalize_snapshot(findings)
    return {
        "level": snapshot.level,
        "summary": _snapshot_summary(snapshot),
        "findings": snapshot.findings,
    }


def _build_task_context(task: Any, tasks_by_id: dict[str, Any],
                        agents_by_id: dict[str, Any],
                        *, target_agent_id: str = "") -> dict:
    family = _task_family(task)
    labels = _stable_labels(getattr(task, "labels", []) or [])
    agent = agents_by_id.get(target_agent_id or getattr(task, "agent_id", "") or "")
    inherited = None
    if not agent:
        inherited = _find_lineage_agent(task, tasks_by_id, agents_by_id)
        agent = inherited
    changed_files = sorted(set(getattr(agent, "worktree_changed_files", []) or []))
    return {
        "task_id": getattr(task, "id", ""),
        "title": getattr(task, "task", ""),
        "group": getattr(task, "group", ""),
        "lane": getattr(task, "lane", ""),
        "action": getattr(task, "action_name", "") or "",
        "family": family,
        "labels": labels,
        "pipeline_root_id": getattr(task, "pipeline_root_id", "") or getattr(task, "id", ""),
        "parent_task_id": getattr(task, "parent_task_id", "") or "",
        "agent_id": getattr(agent, "id", "") if agent else "",
        "task_agent_id": getattr(task, "agent_id", "") or "",
        "worktree_branch": getattr(agent, "worktree_branch", "") if agent else "",
        "worktree_repo_root": getattr(agent, "worktree_repo_root", "") if agent else "",
        "changed_files": changed_files,
        "changed_modules": _module_keys(changed_files),
        "inherited_lineage": bool(inherited and not target_agent_id
                                   and not getattr(task, "agent_id", "")),
    }


def _find_lineage_agent(task: Any, tasks_by_id: dict[str, Any],
                        agents_by_id: dict[str, Any]):
    parent_id = getattr(task, "parent_task_id", "") or ""
    visited = set()
    while parent_id and parent_id not in visited:
        visited.add(parent_id)
        parent = tasks_by_id.get(parent_id)
        if not parent:
            break
        agent_id = getattr(parent, "agent_id", "") or ""
        agent = agents_by_id.get(agent_id)
        if agent and (
                getattr(agent, "worktree_branch", "")
                or getattr(agent, "worktree_path", "")):
            return agent
        parent_id = getattr(parent, "parent_task_id", "") or ""
    return None


def _task_family(task: Any) -> str:
    normalized = _normalize_task_text(getattr(task, "task", "") or "")
    return _topic_key(normalized)


def _module_keys(paths: list[str]) -> list[str]:
    modules = []
    for path in paths:
        parts = [part for part in (path or "").split("/") if part]
        if not parts:
            continue
        if len(parts) == 1:
            key = parts[0]
        else:
            key = "/".join(parts[:2])
        if key not in modules:
            modules.append(key)
    return modules


def _is_sequential_same_agent(ctx: dict, other_ctx: dict) -> bool:
    if not ctx["agent_id"] or ctx["agent_id"] != other_ctx["agent_id"]:
        return False
    return True


def _is_dispatch_sequential(ctx: dict, other_ctx: dict,
                            create_agent: bool) -> bool:
    if create_agent:
        return False
    if not ctx["agent_id"] or ctx["agent_id"] != other_ctx["agent_id"]:
        return False
    return True


def _pair_findings(ctx: dict, other: dict, *,
                   sequential: bool,
                   pending_dispatch: bool = False) -> list[dict]:
    findings = []
    other_lane = other.get("lane", "")
    other_agent = other.get("agent_id", "")

    if (
        ctx["worktree_branch"]
        and other["worktree_branch"]
        and ctx["worktree_branch"] == other["worktree_branch"]
        and ctx["worktree_repo_root"]
        and ctx["worktree_repo_root"] == other["worktree_repo_root"]
        and ctx["task_id"] != other["task_id"]
        and ctx["agent_id"]
        and other_agent
        and ctx["agent_id"] != other_agent
    ):
        findings.append(_finding(
            OVERLAP_CONFLICT,
            "shared_worktree_lineage",
            ctx,
            other,
            "Shares the same worktree branch as another active agent.",
        ))

    shared_files = sorted(set(ctx["changed_files"]) & set(other["changed_files"]))
    if shared_files and ctx["agent_id"] and other_agent and ctx["agent_id"] != other_agent:
        findings.append(_finding(
            OVERLAP_CONFLICT if other_lane == "In Progress" else OVERLAP_WARNING,
            "shared_files",
            ctx,
            other,
            "Touches the same changed files as another task.",
            shared_paths=shared_files[:3],
        ))

    shared_modules = sorted(set(ctx["changed_modules"]) & set(other["changed_modules"]))
    if shared_modules and ctx["agent_id"] and other_agent and ctx["agent_id"] != other_agent:
        findings.append(_finding(
            OVERLAP_WARNING,
            "shared_modules",
            ctx,
            other,
            "Touches the same module area as another task.",
            shared_modules=shared_modules[:3],
        ))

    same_surface = (
        ctx["family"] and ctx["family"] == other["family"]
        or (ctx["action"] and ctx["action"] == other["action"])
    )
    if same_surface:
        if ctx["pipeline_root_id"] and ctx["pipeline_root_id"] == other["pipeline_root_id"]:
            base_level = OVERLAP_NOTICE if sequential else OVERLAP_WARNING
            findings.append(_finding(
                base_level,
                "shared_pipeline_surface",
                ctx,
                other,
                "Shares the same pipeline lineage as another open task.",
            ))

        elif other_lane in {"To Do", "In Progress"}:
            base_level = OVERLAP_NOTICE if sequential else OVERLAP_WARNING
            if pending_dispatch and other_lane == "In Progress" and not sequential:
                base_level = OVERLAP_WARNING
            findings.append(_finding(
                base_level,
                "existing_same_surface_work",
                ctx,
                other,
                "Another open task already targets the same surface.",
            ))
        else:
            findings.append(_finding(
                OVERLAP_NOTICE,
                "same_action_or_family",
                ctx,
                other,
                "Matches another task's action or task area.",
            ))

    shared_labels = sorted(set(ctx["labels"]) & set(other["labels"]))
    if shared_labels:
        findings.append(_finding(
            OVERLAP_NOTICE,
            "shared_labels",
            ctx,
            other,
            "Shares stable labels with another open task.",
            shared_labels=shared_labels[:3],
        ))

    return findings


def _history_findings(ctx: dict, history_rows: list[dict]) -> list[dict]:
    findings = []
    seen = set()
    for row in history_rows:
        if row.get("task_id") == ctx["task_id"]:
            continue
        if row.get("family") and row.get("family") != ctx["family"] \
                and row.get("action") != ctx["action"]:
            continue
        branch = row.get("worktree_branch", "") or ""
        if not branch:
            continue
        key = (row.get("task_id", ""), branch)
        if key in seen:
            continue
        seen.add(key)
        findings.append({
            "level": OVERLAP_NOTICE,
            "reason_code": "recent_branch_history",
            "message": "Recent work on the same surface used a different branch.",
            "task_id": row.get("task_id", ""),
            "task_title": row.get("task_title", ""),
            "agent_id": row.get("agent_id", ""),
            "worktree_branch": branch,
            "started_at": row.get("started_at", 0),
        })
    return findings[:2]


def _load_recent_history(tasks_by_id: dict[str, Any], db) -> dict[str, list[dict]]:
    if not db:
        return {}
    try:
        cutoff = time.time() - _HISTORY_WINDOW_SECS
        history_agents = {
            row["id"]: row for row in db.load_all_agent_history_records()
        }
        grouped: defaultdict[str, list] = defaultdict(list)
        for row in db.load_all_agent_tasks():
            if (row.get("started_at") or 0) < cutoff:
                continue
            task = tasks_by_id.get(row.get("task_id", ""))
            if not task:
                continue
            agent_meta = history_agents.get(row.get("agent_id", ""), {})
            grouped[getattr(task, "group", "")].append({
                "task_id": row.get("task_id", ""),
                "task_title": row.get("task_title", ""),
                "agent_id": row.get("agent_id", ""),
                "started_at": row.get("started_at", 0),
                "worktree_branch": agent_meta.get("worktree_branch", ""),
                "family": _task_family(task),
                "action": getattr(task, "action_name", "") or "",
            })
        return grouped
    except Exception:
        return {}


def _finding(level: str, reason_code: str, ctx: dict, other: dict,
             message: str, **extra) -> dict:
    return {
        "level": level,
        "reason_code": reason_code,
        "message": message,
        "task_id": other.get("task_id", ""),
        "task_title": other.get("title", ""),
        "agent_id": other.get("agent_id", ""),
        "lane": other.get("lane", ""),
        **extra,
    }


def _finalize_snapshot(findings: list[dict]) -> DispatchOverlapSnapshot:
    if not findings:
        return DispatchOverlapSnapshot(level=OVERLAP_NONE, findings=[])
    deduped = {}
    for finding in findings:
        key = (
            finding.get("reason_code", ""),
            finding.get("task_id", ""),
            finding.get("agent_id", ""),
            tuple(finding.get("shared_paths", []) or []),
            tuple(finding.get("shared_modules", []) or []),
        )
        prior = deduped.get(key)
        if not prior or OVERLAP_SEVERITY[finding["level"]] > OVERLAP_SEVERITY[prior["level"]]:
            deduped[key] = finding
    ordered = sorted(deduped.values(), key=lambda item: (
        -OVERLAP_SEVERITY.get(item.get("level", OVERLAP_NONE), 0),
        item.get("task_title", "").lower(),
        item.get("reason_code", ""),
    ))
    level = ordered[0]["level"] if ordered else OVERLAP_NONE
    return DispatchOverlapSnapshot(level=level, findings=ordered[:_MAX_FINDINGS])


def _snapshot_summary(snapshot: DispatchOverlapSnapshot) -> str:
    if not snapshot.findings:
        return ""
    primary = snapshot.findings[0]
    title = primary.get("task_title", "")
    message = primary.get("message", "")
    if title:
        return f"{message} Related task: {title}."
    return message
