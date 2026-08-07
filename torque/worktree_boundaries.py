"""Helpers for task-scoped worktree merge boundaries."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Iterable


def branch_key(repo_root: str, branch: str) -> str:
    return f"{repo_root.strip()}::{branch.strip()}"


def task_boundary(task) -> dict:
    boundary = getattr(task, "worktree_boundary", {}) or {}
    return boundary if isinstance(boundary, dict) else {}


_PR_STATE_ALIASES = {
    "created": "open",
    "failed": "blocked",
    "merge_failed": "blocked",
    "pending": "auto_merge_enabled",
}
_PR_CLEANUP_KEYS = (
    "close_agent_on_merge",
    "remove_worktree_on_merge",
    "auto_move_to_done",
    "preserve_merge_diff",
)


def _clean_text(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()


# Durable code-ownership classification recorded when a task boundary is made.
# Done evaluation intentionally consumes only this stored fact; it never probes
# a live branch/worktree, which may legitimately have been cleaned up on merge.
CODE_DELTA_PRESENT = "present"
CODE_DELTA_ABSENT = "absent"
CODE_DELTA_UNKNOWN = "unknown"
CODE_DELTA_LEGACY_UNCLASSIFIED = "legacy_unclassified"


def boundary_code_delta_state(boundary: dict | None) -> str:
    """Return the stored code-delta state, conservatively handling old rows.

    No boundary at all means this pipeline has not asserted a code-bearing
    boundary and remains an ordinary no-code root. A boundary with no valid
    classifier fact is legacy/incomplete evidence and must not look absent.
    """
    if not isinstance(boundary, dict) or not boundary:
        return CODE_DELTA_ABSENT
    fact = boundary.get("code_delta")
    if not isinstance(fact, dict):
        return CODE_DELTA_LEGACY_UNCLASSIFIED
    state = _clean_text(fact.get("state", "")).lower()
    if state in {CODE_DELTA_PRESENT, CODE_DELTA_ABSENT, CODE_DELTA_UNKNOWN}:
        return state
    return CODE_DELTA_LEGACY_UNCLASSIFIED


def boundary_is_durably_merged(boundary: dict | None) -> bool:
    """True only for persisted merge status *and* merge SHA evidence."""
    if not isinstance(boundary, dict):
        return False
    if _clean_text(boundary.get("status", "")).lower() != "merged":
        return False
    return bool(_clean_text(boundary.get("merge_commit_sha", "")))


def code_boundary_done_status(tasks: Iterable) -> dict:
    """Evaluate the common Done gate for a root pipeline's boundaries.

    ``present`` is code ownership. ``unknown`` and old unclassified facts are
    open-until-merged, while durable merge evidence wins after cleanup so old
    records do not become permanently unsatisfiable.
    """
    blocking = []
    present = []
    unknown = []
    for task in tasks:
        boundary = task_boundary(task)
        if not boundary:
            continue
        state = boundary_code_delta_state(boundary)
        merged = boundary_is_durably_merged(boundary)
        item = {
            "task_id": _clean_text(getattr(task, "id", "")),
            "state": state,
            "merged": merged,
        }
        if state == CODE_DELTA_PRESENT:
            present.append(item)
        elif state in {CODE_DELTA_UNKNOWN, CODE_DELTA_LEGACY_UNCLASSIFIED}:
            unknown.append(item)
        if state != CODE_DELTA_ABSENT and not merged:
            blocking.append(item)
    return {"eligible": not blocking, "present": present,
            "unknown": unknown, "blocking": blocking}


async def classify_boundary_code_delta(*, worktree_path: str, base_branch: str,
                                       commit_sha: str) -> dict:
    """Classify a checkpointed boundary against its branch fork point.

    Standard Git tree diff intentionally includes gitlink changes.  Any Git
    failure is stored as ``unknown``; callers must persist this result rather
    than re-trying from a later, potentially cleaned-up worktree.
    """
    classified_at = datetime.now(timezone.utc).isoformat()
    base_branch = _clean_text(base_branch)
    commit_sha = _clean_text(commit_sha)
    if not _clean_text(worktree_path) or not base_branch or not commit_sha:
        return {"state": CODE_DELTA_UNKNOWN, "reason": "missing_boundary_inputs",
                "commit_sha": commit_sha, "classified_at": classified_at}
    async def run(*args):
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", worktree_path, *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            return proc.returncode, stdout.decode("utf-8", errors="replace")
        except Exception:
            return 1, ""
    code, fork = await run("merge-base", base_branch, commit_sha)
    fork = fork.strip()
    if code or not fork:
        return {"state": CODE_DELTA_UNKNOWN, "reason": "merge_base_failed",
                "commit_sha": commit_sha, "classified_at": classified_at}
    code, names = await run("diff", "--name-only", fork, commit_sha)
    if code:
        return {"state": CODE_DELTA_UNKNOWN, "reason": "diff_failed",
                "base_sha": fork, "commit_sha": commit_sha,
                "classified_at": classified_at}
    paths = [line for line in names.splitlines() if line.strip()]
    return {"state": CODE_DELTA_PRESENT if paths else CODE_DELTA_ABSENT,
            "base_sha": fork, "commit_sha": commit_sha,
            "comparison": "fork_point..boundary", "path_count": len(paths),
            "classified_at": classified_at}

def _normalize_pr_state(value, *, pending: bool | None = None) -> str:
    state = _clean_text(value).lower()
    if not state:
        if pending is True:
            return "auto_merge_enabled"
        return ""
    return _PR_STATE_ALIASES.get(state, state)


def _normalize_pr_number(value):
    if value in {"", None}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _normalize_requested_cleanup(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        key: bool(value[key])
        for key in _PR_CLEANUP_KEYS
        if key in value
    }


def boundary_pr_metadata(boundary: dict | None) -> dict:
    """Return normalized PR metadata already attached to a boundary."""
    if not isinstance(boundary, dict):
        return {}

    raw = boundary.get("pr")
    if not isinstance(raw, dict):
        # Compatibility with the first server PR-flow implementation and any
        # in-flight boundaries created before this canonical helper existed.
        raw = boundary.get("pull_request")
    if not isinstance(raw, dict):
        raw = {}

    raw_state = _normalize_pr_state(
        raw.get("state", ""),
        pending=raw.get("pending") if "pending" in raw else None,
    )
    status_state = _normalize_pr_state(
        raw.get("status", ""),
        pending=raw.get("pending") if "pending" in raw else None,
    )
    state = raw_state or status_state
    if status_state in {"auto_merge_enabled", "blocked", "merged"} \
            and raw_state in {"", "open"}:
        state = status_state

    pr = {
        "provider": _clean_text(raw.get("provider", "")),
        "remote": _clean_text(raw.get("remote", "")),
        "base_branch": _clean_text(raw.get("base_branch", "")),
        "head_branch": _clean_text(
            raw.get("head_branch", "") or raw.get("branch", "")
        ),
        "head_sha": _clean_text(raw.get("head_sha", "")),
        "url": _clean_text(raw.get("url", "")),
        "number": _normalize_pr_number(raw.get("number")),
        "state": state,
        "merge_state": _clean_text(raw.get("merge_state", "")),
        "created_at": _clean_text(raw.get("created_at", "")),
        "updated_at": _clean_text(raw.get("updated_at", "")),
        "merged_at": _clean_text(raw.get("merged_at", "")),
        "merge_commit_sha": _clean_text(raw.get("merge_commit_sha", "")),
        "requested_cleanup": _normalize_requested_cleanup(
            raw.get("requested_cleanup", {})
        ),
    }

    # Legacy top-level fields from the first PR-flow cut.
    if not pr["url"]:
        pr["url"] = _clean_text(boundary.get("pr_url", ""))
    if pr["number"] is None:
        pr["number"] = _normalize_pr_number(boundary.get("pr_number"))
    if not pr["head_sha"]:
        pr["head_sha"] = _clean_text(boundary.get("pr_head_sha", ""))
    if not pr["state"]:
        pr["state"] = _normalize_pr_state(
            boundary.get("pr_state", "") or boundary.get("pr_status", ""),
            pending=boundary.get("pr_pending")
            if "pr_pending" in boundary else None,
        )
    if not pr["merge_state"]:
        pr["merge_state"] = _clean_text(boundary.get("pr_merge_state", ""))
    if not pr["updated_at"]:
        pr["updated_at"] = _clean_text(boundary.get("pr_updated_at", ""))

    return {
        key: value
        for key, value in pr.items()
        if value not in ("", None, {})
    }


def normalize_pr_metadata_for_boundary(
    pr_metadata: dict | None,
    *,
    existing: dict | None = None,
    requested_cleanup: dict | None = None,
    now: str | None = None,
) -> dict:
    """Merge arbitrary PR helper output into the canonical boundary shape."""
    now = _clean_text(now) or datetime.now(timezone.utc).isoformat()
    base = boundary_pr_metadata({"pr": existing or {}})
    incoming = dict(pr_metadata or {})

    cleanup = dict(base.get("requested_cleanup", {}) or {})
    cleanup.update(
        _normalize_requested_cleanup(incoming.get("requested_cleanup", {}))
    )
    cleanup.update(_normalize_requested_cleanup(requested_cleanup or {}))

    pending = incoming.get("pending") if "pending" in incoming else None
    incoming_state = _normalize_pr_state(incoming.get("state", ""), pending=pending)
    status_state = _normalize_pr_state(incoming.get("status", ""), pending=pending)
    base_state = _normalize_pr_state(base.get("state", ""), pending=pending)
    state = incoming_state or status_state or base_state
    if status_state in {"auto_merge_enabled", "blocked", "merged"} \
            and incoming_state in {"", "open"}:
        state = status_state
    if not state:
        state = "auto_merge_enabled" if pending is True else "open"

    created_at = (
        _clean_text(incoming.get("created_at", ""))
        or _clean_text(base.get("created_at", ""))
        or now
    )
    updated_at = (
        _clean_text(incoming.get("updated_at", ""))
        or _clean_text(base.get("updated_at", ""))
        or now
    )

    pr = {
        "provider": (
            _clean_text(incoming.get("provider", ""))
            or _clean_text(base.get("provider", ""))
            or "github"
        ),
        "remote": _clean_text(incoming.get("remote", "")) or base.get("remote", ""),
        "base_branch": (
            _clean_text(incoming.get("base_branch", ""))
            or base.get("base_branch", "")
        ),
        "head_branch": (
            _clean_text(incoming.get("head_branch", ""))
            or _clean_text(incoming.get("branch", ""))
            or base.get("head_branch", "")
        ),
        "head_sha": (
            _clean_text(incoming.get("head_sha", ""))
            or base.get("head_sha", "")
        ),
        "url": _clean_text(incoming.get("url", "")) or base.get("url", ""),
        "number": _normalize_pr_number(
            incoming.get("number")
            if incoming.get("number") not in {"", None}
            else base.get("number")
        ),
        "state": state,
        "merge_state": (
            _clean_text(incoming.get("merge_state", ""))
            or base.get("merge_state", "")
        ),
        "created_at": created_at,
        "updated_at": updated_at,
        "merged_at": (
            _clean_text(incoming.get("merged_at", ""))
            or base.get("merged_at", "")
        ),
        "merge_commit_sha": (
            _clean_text(incoming.get("merge_commit_sha", ""))
            or base.get("merge_commit_sha", "")
        ),
    }
    if cleanup:
        pr["requested_cleanup"] = cleanup
    return {
        key: value
        for key, value in pr.items()
        if value not in ("", None, {})
    }


def attach_pr_metadata_to_boundary(
    boundary: dict | None,
    pr_metadata: dict,
    *,
    requested_cleanup: dict | None = None,
    now: str | None = None,
) -> dict:
    """Return a boundary copy with canonical PR metadata attached."""
    out = dict(boundary or {})
    out["pr"] = normalize_pr_metadata_for_boundary(
        pr_metadata,
        existing=boundary_pr_metadata(out),
        requested_cleanup=requested_cleanup,
        now=now,
    )
    # Drop early transitional/legacy PR fields now that the canonical nested
    # shape is present. Everything useful was folded into ``pr`` above.
    for key in (
        "pull_request",
        "pr_url",
        "pr_number",
        "pr_head_sha",
        "pr_state",
        "pr_merge_state",
        "pr_pending",
        "pr_status",
        "pr_updated_at",
    ):
        out.pop(key, None)
    return out


def attach_pr_metadata_to_latest_open_boundary(
    tasks: Iterable,
    *,
    repo_root: str,
    branch: str,
    pr_metadata: dict,
    requested_cleanup: dict | None = None,
    now: str | None = None,
):
    """Attach/update PR metadata on the latest open boundary for a branch."""
    latest = latest_boundary_task(
        tasks,
        repo_root=repo_root,
        branch=branch,
        statuses={"open"},
    )
    if not latest:
        return None
    latest.worktree_boundary = attach_pr_metadata_to_boundary(
        task_boundary(latest),
        pr_metadata,
        requested_cleanup=requested_cleanup,
        now=now,
    )
    return latest


def task_boundary_status(task) -> str:
    return str(task_boundary(task).get("status", "") or "")


def task_has_open_boundary(task) -> bool:
    return task_boundary_status(task) == "open"


def task_branch_key(task) -> str:
    boundary = task_boundary(task)
    repo_root = boundary.get("repo_root", "")
    branch = boundary.get("branch", "")
    if not repo_root or not branch:
        return ""
    return branch_key(repo_root, branch)


def boundary_submodule_branches(boundary: dict | None) -> list[dict]:
    """Return normalized nested-submodule branch entries on a boundary."""
    if not isinstance(boundary, dict):
        return []
    raw = boundary.get("submodules", [])
    if not isinstance(raw, list):
        raw = boundary.get("submodule_branches", [])
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = _clean_text(item.get("path", ""))
        repo_root = _clean_text(item.get("repo_root", ""))
        branch = _clean_text(item.get("branch", ""))
        commit_sha = _clean_text(
            item.get("commit_sha", "") or item.get("head_sha", "")
        )
        if not path or not repo_root or not branch:
            continue
        out.append({
            "path": path,
            "repo_root": repo_root,
            "branch": branch,
            "base_branch": _clean_text(item.get("base_branch", "")),
            "commit_sha": commit_sha,
            "gitlink_sha": _clean_text(item.get("gitlink_sha", "")),
        })
    return out


def task_branch_keys(task, *, include_submodules: bool = False) -> set[str]:
    """Return primary and, optionally, nested-submodule branch keys."""
    keys = set()
    key = task_branch_key(task)
    if key:
        keys.add(key)
    if include_submodules:
        for sub in boundary_submodule_branches(task_boundary(task)):
            keys.add(branch_key(sub.get("repo_root", ""), sub.get("branch", "")))
    return keys


def boundary_sort_key(task) -> tuple[str, str]:
    boundary = task_boundary(task)
    return (
        str(boundary.get("recorded_at", "") or ""),
        str(getattr(task, "updated_at", "") or ""),
    )


def branch_boundary_tasks(tasks: Iterable, *,
                          repo_root: str,
                          branch: str,
                          statuses: set[str] | None = None,
                          include_submodules: bool = False) -> list:
    wanted = branch_key(repo_root, branch)
    matched = []
    for task in tasks:
        if include_submodules:
            if wanted not in task_branch_keys(task, include_submodules=True):
                continue
        elif task_branch_key(task) != wanted:
            continue
        if statuses:
            status = task_boundary(task).get("status", "") or ""
            if status not in statuses:
                continue
        matched.append(task)
    matched.sort(key=boundary_sort_key)
    return matched


def latest_boundary_task(tasks: Iterable, *,
                         repo_root: str,
                         branch: str,
                         statuses: set[str] | None = None,
                         include_submodules: bool = False):
    matched = branch_boundary_tasks(
        tasks,
        repo_root=repo_root,
        branch=branch,
        statuses=statuses,
        include_submodules=include_submodules,
    )
    if not matched:
        return None
    return matched[-1]


def latest_boundary_base_branch(tasks: Iterable, *,
                                repo_root: str,
                                branch: str,
                                statuses: set[str] | None = None) -> str:
    latest = latest_boundary_task(
        tasks,
        repo_root=repo_root,
        branch=branch,
        statuses=statuses,
    )
    if not latest:
        return ""
    return str(task_boundary(latest).get("base_branch", "") or "").strip()


def successor_tasks(tasks: Iterable, boundary_task_id: str) -> list:
    matched = [
        task for task in tasks
        if getattr(task, "resume_after_boundary_task_id", "") == boundary_task_id
    ]
    matched.sort(
        key=lambda task: (
            str(getattr(task, "created_at", "") or ""),
            str(getattr(task, "updated_at", "") or ""),
        )
    )
    return matched


def queued_successor_tasks(tasks: Iterable, boundary_task_id: str) -> list:
    return [
        task for task in successor_tasks(tasks, boundary_task_id)
        if getattr(task, "lane", "") in {"Backlog", "To Do"}
    ]


def started_successor_tasks(tasks: Iterable, boundary_task_id: str) -> list:
    return [
        task for task in successor_tasks(tasks, boundary_task_id)
        if getattr(task, "lane", "") not in {"Backlog", "To Do"}
    ]


def retarget_queued_successor_tasks(tasks: Iterable, *,
                                    agent_id: str,
                                    boundary_task_id: str,
                                    exclude_task_id: str = "") -> list:
    updated = []
    for task in tasks:
        if getattr(task, "id", "") == exclude_task_id:
            continue
        if getattr(task, "agent_id", "") != agent_id:
            continue
        if getattr(task, "lane", "") not in {"Backlog", "To Do"}:
            continue
        if getattr(task, "resume_after_boundary_task_id", "") == boundary_task_id:
            continue
        task.resume_after_boundary_task_id = boundary_task_id
        updated.append(task)
    return updated


def clear_stale_successor_references(tasks: Iterable) -> list:
    tasks_by_id = {
        getattr(task, "id", ""): task
        for task in tasks
        if getattr(task, "id", "")
    }
    updated = []
    for task in tasks_by_id.values():
        boundary_task_id = (
            getattr(task, "resume_after_boundary_task_id", "") or ""
        )
        if not boundary_task_id:
            continue
        boundary_task = tasks_by_id.get(boundary_task_id)
        if boundary_task and task_has_open_boundary(boundary_task):
            continue
        task.resume_after_boundary_task_id = ""
        updated.append(task)
    return updated


def boundary_summary(task, *, queued_followers: list | None = None,
                     started_followers: list | None = None) -> dict:
    boundary = task_boundary(task)
    queued_followers = queued_followers or []
    started_followers = started_followers or []
    return {
        "task_id": getattr(task, "id", ""),
        "task_title": getattr(task, "task", ""),
        "task_slug": getattr(task, "slug", ""),
        "action_name": getattr(task, "action_name", ""),
        "lane": getattr(task, "lane", ""),
        "boundary": dict(boundary),
        "queued_followers": [
            {
                "task_id": getattr(follower, "id", ""),
                "task_title": getattr(follower, "task", ""),
                "lane": getattr(follower, "lane", ""),
            }
            for follower in queued_followers
        ],
        "started_followers": [
            {
                "task_id": getattr(follower, "id", ""),
                "task_title": getattr(follower, "task", ""),
                "lane": getattr(follower, "lane", ""),
            }
            for follower in started_followers
        ],
    }


async def refresh_latest_boundary_after_rebase(
    tasks: Iterable,
    *,
    repo_root: str,
    branch: str,
    worktree_path: str,
    previous_head_sha: str,
    rebased_head_sha: str,
    previous_submodules: list[dict] | None = None,
    rebased_submodules: list[dict] | None = None,
):
    """Re-anchor the latest open boundary after a Torque-managed rebase.

    This only updates boundaries that still pointed at the exact pre-rebase
    branch tip and have no started successor tasks. That preserves the normal
    safety model for stale or already-continued histories while letting Torque's
    own clean rebases keep the merge boundary current. The code-delta fact is
    recomputed against the rebased head and replaced as one classifier result,
    including an honest ``unknown`` result when Git classification fails.
    """
    previous_head_sha = str(previous_head_sha or "").strip()
    rebased_head_sha = str(rebased_head_sha or "").strip()
    if not repo_root or not branch:
        return None
    if not previous_head_sha or not rebased_head_sha:
        return None
    if previous_head_sha == rebased_head_sha:
        return None

    latest = latest_boundary_task(
        tasks,
        repo_root=repo_root,
        branch=branch,
        statuses={"open"},
    )
    if not latest:
        return None

    source_boundary = dict(task_boundary(latest))
    if not source_boundary:
        return None
    if source_boundary.get("commit_sha", "") != previous_head_sha:
        return None
    if started_successor_tasks(tasks, getattr(latest, "id", "")):
        return None

    boundary = dict(source_boundary)
    if previous_submodules is not None or rebased_submodules is not None:
        previous_by_path = {
            _clean_text(item.get("path", "")): item
            for item in (previous_submodules or [])
            if isinstance(item, dict) and _clean_text(item.get("path", ""))
        }
        rebased_by_path = {
            _clean_text(item.get("path", "")): item
            for item in (rebased_submodules or [])
            if isinstance(item, dict) and _clean_text(item.get("path", ""))
        }
        updated_submodules = []
        for existing in boundary_submodule_branches(boundary):
            path = existing.get("path", "")
            previous = previous_by_path.get(path, {})
            rebased = rebased_by_path.get(path, {})
            expected = _clean_text(
                previous.get("commit_sha", "") or previous.get("head_sha", "")
            )
            if expected and existing.get("commit_sha", "") != expected:
                return None
            if rebased:
                merged = dict(existing)
                merged.update({
                    key: value
                    for key, value in rebased.items()
                    if value not in ("", None, {})
                })
                updated_submodules.append(merged)
            else:
                updated_submodules.append(existing)
        if updated_submodules:
            boundary["submodules"] = updated_submodules
    code_delta = await classify_boundary_code_delta(
        worktree_path=worktree_path,
        base_branch=boundary.get("base_branch", ""),
        commit_sha=rebased_head_sha,
    )
    # Classification yields to Git. Recheck the selection before assigning so
    # a concurrent supersession or successor start cannot be overwritten.
    if latest_boundary_task(
        tasks,
        repo_root=repo_root,
        branch=branch,
        statuses={"open"},
    ) is not latest:
        return None
    if dict(task_boundary(latest)) != source_boundary:
        return None
    if started_successor_tasks(tasks, getattr(latest, "id", "")):
        return None
    boundary["commit_sha"] = rebased_head_sha
    boundary["code_delta"] = code_delta
    boundary.pop("reason", None)
    latest.worktree_boundary = boundary
    return latest



def advance_latest_boundary_after_mechanical_commit(
    tasks: Iterable,
    *,
    repo_root: str,
    branch: str,
    expected_previous_head: str,
    new_head: str,
    machine_verification: dict,
    actor_agent_id: str = "",
    reason: str = "verified_mechanical_gitlink",
    verification_note: str = "",
    now: str | None = None,
):
    """Advance the latest open boundary after a machine-verified gitlink commit.

    The human note is audit metadata only. Authorization comes exclusively
    from ``machine_verification['ok']`` plus boundary/successor checks here.
    """
    repo_root = _clean_text(repo_root)
    branch = _clean_text(branch)
    expected_previous_head = _clean_text(expected_previous_head)
    new_head = _clean_text(new_head)
    verification_note = _clean_text(verification_note)
    reason = _clean_text(reason) or "verified_mechanical_gitlink"
    now = _clean_text(now) or datetime.now(timezone.utc).isoformat()
    if not repo_root or not branch:
        return None, {"ok": False, "reason": "missing_target"}
    if not expected_previous_head or not new_head:
        return None, {"ok": False, "reason": "missing_head"}
    if not verification_note:
        return None, {"ok": False, "reason": "verification_note_required"}
    if not isinstance(machine_verification, dict) or not machine_verification.get("ok"):
        return None, {
            "ok": False,
            "reason": "machine_verification_failed",
            "machine_verification": machine_verification or {},
        }

    latest = latest_boundary_task(
        tasks,
        repo_root=repo_root,
        branch=branch,
        statuses={"open"},
    )
    if not latest:
        return None, {"ok": False, "reason": "no_latest_open_boundary"}
    boundary = dict(task_boundary(latest))
    if boundary.get("commit_sha", "") != expected_previous_head:
        return None, {
            "ok": False,
            "reason": "boundary_head_mismatch",
            "boundary_head": boundary.get("commit_sha", ""),
            "expected_previous_head": expected_previous_head,
        }
    started = started_successor_tasks(tasks, getattr(latest, "id", ""))
    if started:
        return None, {
            "ok": False,
            "reason": "started_successor",
            "started_successors": [getattr(task, "id", "") for task in started],
        }

    paths = list(machine_verification.get("paths", []) or [])
    updated_submodules = machine_verification.get("submodules", []) or []
    boundary["commit_sha"] = new_head
    boundary.pop("reason", None)
    if updated_submodules:
        boundary["submodules"] = updated_submodules
    advances = list(boundary.get("mechanical_advances", []) or [])
    advances.append({
        "advanced_at": now,
        "actor_agent_id": _clean_text(actor_agent_id),
        "reason": reason,
        "verification_note": verification_note,
        "previous_head": expected_previous_head,
        "new_head": new_head,
        "mechanical_commit": _clean_text(
            machine_verification.get("mechanical_commit", "")
        ),
        "paths": paths,
    })
    boundary["mechanical_advances"] = advances
    latest.worktree_boundary = boundary
    return latest, {
        "ok": True,
        "task_id": getattr(latest, "id", ""),
        "previous_head": expected_previous_head,
        "new_head": new_head,
        "mechanical_commit": _clean_text(
            machine_verification.get("mechanical_commit", "")
        ),
        "paths": paths,
    }

def _normalize_boundary_task_ids(task_ids: Iterable[str] | str) -> tuple[str, ...]:
    if isinstance(task_ids, str):
        return (task_ids,)
    return tuple(task_ids or ())


def _task_root_id(task, tasks_by_id: dict[str, object]) -> str:
    root_id = _clean_text(getattr(task, "pipeline_root_id", ""))
    if root_id:
        return root_id
    parent_id = _clean_text(getattr(task, "parent_task_id", ""))
    if not parent_id:
        return _clean_text(getattr(task, "id", ""))

    seen: set[str] = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = tasks_by_id.get(parent_id)
        if not parent:
            return parent_id
        next_parent_id = _clean_text(getattr(parent, "parent_task_id", ""))
        if not next_parent_id:
            return _clean_text(getattr(parent, "id", "")) or parent_id
        parent_id = next_parent_id
    return ""


def review_cycle_containment_candidates(tasks: Iterable, *,
                                        repo_root: str,
                                        branch: str,
                                        task_ids: Iterable[str] | str,
                                        ) -> list:
    """Return only structurally linked earlier review-cycle boundaries.

    Repository and branch identity may reject an audited reciprocal link, but
    can never create one.  Walking backward starts at the explicitly selected
    implementation plus its direct shipping review, so branch siblings and
    reroute victims never become containment candidates.
    """
    tasks = list(tasks)
    tasks_by_id = {
        _clean_text(getattr(task, "id", "")): task
        for task in tasks
        if _clean_text(getattr(task, "id", ""))
    }
    target_ids = {
        _clean_text(task_id)
        for task_id in _normalize_boundary_task_ids(task_ids)
        if _clean_text(task_id)
    }
    repo_root = _clean_text(repo_root)
    branch = _clean_text(branch)
    if not repo_root or not branch or not target_ids:
        return []

    pairs: list[tuple[str, str]] = []
    for continuation_id in target_ids:
        continuation = tasks_by_id.get(continuation_id)
        if not continuation or _clean_text(
                getattr(continuation, "action_name", "")).lower() \
                != "feature/implement":
            continue
        continuation_root = _task_root_id(continuation, tasks_by_id)
        for candidate in tasks:
            candidate_id = _clean_text(getattr(candidate, "id", ""))
            if (
                    candidate_id in target_ids
                    and _clean_text(
                        getattr(candidate, "action_name", "")
                    ).lower() == "feature/review"
                    and _clean_text(
                        getattr(candidate, "parent_task_id", "")
                    ) == continuation_id
                    and _task_root_id(candidate, tasks_by_id)
                    == continuation_root
            ):
                pairs.append((continuation_id, candidate_id))

    candidates: list = []
    candidate_ids: set[str] = set()
    visited_pairs: set[tuple[str, str]] = set()
    while pairs:
        continuation_id, direct_review_id = pairs.pop()
        pair = (continuation_id, direct_review_id)
        if pair in visited_pairs:
            continue
        visited_pairs.add(pair)

        continuation = tasks_by_id.get(continuation_id)
        direct_review = tasks_by_id.get(direct_review_id)
        if (
                not continuation
                or not direct_review
                or _clean_text(
                    getattr(continuation, "action_name", "")
                ).lower() != "feature/implement"
                or _clean_text(
                    getattr(direct_review, "action_name", "")
                ).lower() != "feature/review"
                or _clean_text(
                    getattr(direct_review, "parent_task_id", "")
                ) != continuation_id
                or _task_root_id(direct_review, tasks_by_id)
                != _task_root_id(continuation, tasks_by_id)
        ):
            continue

        evidence = getattr(continuation, "completion_evidence", {}) or {}
        link = (
            evidence.get("review_cycle_continue", {})
            if isinstance(evidence, dict) else {}
        )
        if not isinstance(link, dict):
            continue
        predecessor_id = _clean_text(link.get("original_review_task_id", ""))
        predecessor = tasks_by_id.get(predecessor_id)
        predecessor_boundary = task_boundary(predecessor)
        predecessor_evidence = (
            getattr(predecessor, "completion_evidence", {}) or {}
            if predecessor else {}
        )
        predecessor_links = (
            predecessor_evidence.get("review_cycle_continuations", [])
            if isinstance(predecessor_evidence, dict) else []
        )
        root_id = _task_root_id(continuation, tasks_by_id)
        reciprocal_link = next((
            item for item in predecessor_links
            if isinstance(item, dict)
            and _clean_text(item.get("original_review_task_id", ""))
            == predecessor_id
            and _clean_text(item.get("continuation_task_id", ""))
            == continuation_id
            and _clean_text(item.get("pipeline_root_id", "")) == root_id
            and _clean_text(item.get("repo_root", "")) == repo_root
            and _clean_text(item.get("branch", "")) == branch
        ), None)
        if (
                not predecessor
                or not reciprocal_link
                or _clean_text(
                    getattr(predecessor, "action_name", "")
                ).lower() != "feature/review"
                or _clean_text(
                    predecessor_boundary.get("status", "")
                ).lower() != "superseded"
                or _clean_text(
                    predecessor_boundary.get("superseded_by_task_id", "")
                ) != continuation_id
                or _clean_text(link.get("continuation_task_id", ""))
                != continuation_id
                or _task_root_id(predecessor, tasks_by_id) != root_id
                or _clean_text(predecessor_boundary.get("repo_root", ""))
                != repo_root
                or _clean_text(predecessor_boundary.get("branch", ""))
                != branch
        ):
            continue

        if predecessor_id not in candidate_ids:
            candidates.append(predecessor)
            candidate_ids.add(predecessor_id)

        prior_implementation_id = _clean_text(
            getattr(predecessor, "parent_task_id", "")
        )
        prior_implementation = tasks_by_id.get(prior_implementation_id)
        prior_boundary = task_boundary(prior_implementation)
        if (
                prior_implementation
                and _clean_text(
                    getattr(prior_implementation, "action_name", "")
                ).lower() == "feature/implement"
                and _task_root_id(prior_implementation, tasks_by_id) == root_id
                and _clean_text(prior_boundary.get("status", "")).lower()
                == "superseded"
                and _clean_text(
                    prior_boundary.get("superseded_by_task_id", "")
                ) == predecessor_id
                and _clean_text(prior_boundary.get("repo_root", ""))
                == repo_root
                and _clean_text(prior_boundary.get("branch", "")) == branch
        ):
            if prior_implementation_id not in candidate_ids:
                candidates.append(prior_implementation)
                candidate_ids.add(prior_implementation_id)
            pairs.append((prior_implementation_id, predecessor_id))
    return candidates


async def verified_review_cycle_containment_task_ids(
        tasks: Iterable, *,
        repo_root: str,
        branch: str,
        merge_sha: str,
        task_ids: Iterable[str] | str) -> tuple[str, ...]:
    """Verify which linked earlier boundaries are contained by a merge.

    Ancestry is conclusive.  Squash/rebase-style landings additionally require
    one parent, the exact stored fork, disjoint base movement, and a no-op
    three-way merge of the candidate into the actual landed tree.  Every Git
    or evidence error fails closed for that candidate.
    """
    candidates = review_cycle_containment_candidates(
        tasks,
        repo_root=repo_root,
        branch=branch,
        task_ids=task_ids,
    )
    repo_root = _clean_text(repo_root)
    merge_sha = _clean_text(merge_sha)
    if not candidates or not repo_root or not merge_sha:
        return ()

    async def git(*args: str) -> tuple[int, bytes]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            return proc.returncode, stdout
        except Exception:
            return 1, b""

    async def resolve_commit(ref: str) -> str:
        if not ref:
            return ""
        code, out = await git("rev-parse", "--verify", f"{ref}^{{commit}}")
        if code:
            return ""
        lines = out.decode("ascii", errors="ignore").splitlines()
        return lines[0].strip() if len(lines) == 1 else ""

    merged_commit = await resolve_commit(merge_sha)
    if not merged_commit:
        return ()
    code, parent_out = await git(
        "rev-list", "--parents", "-n", "1", merged_commit
    )
    parent_fields = parent_out.decode("ascii", errors="ignore").split()
    landing_parent = (
        parent_fields[1]
        if code == 0 and len(parent_fields) == 2
        else ""
    )
    code, merged_tree_out = await git(
        "rev-parse", "--verify", f"{merged_commit}^{{tree}}"
    )
    merged_tree_lines = merged_tree_out.decode(
        "ascii", errors="ignore"
    ).splitlines()
    merged_tree = (
        merged_tree_lines[0].strip()
        if code == 0 and len(merged_tree_lines) == 1
        else ""
    )

    verified: list[str] = []
    for task in candidates:
        boundary = task_boundary(task)
        fact = boundary.get("code_delta")
        if (
                boundary_code_delta_state(boundary) != CODE_DELTA_PRESENT
                or not isinstance(fact, dict)
        ):
            continue
        base_ref = _clean_text(fact.get("base_sha", ""))
        boundary_ref = _clean_text(boundary.get("commit_sha", ""))
        classified_ref = _clean_text(fact.get("commit_sha", ""))
        if not base_ref or not boundary_ref or (
                classified_ref and classified_ref != boundary_ref):
            continue
        base_commit = await resolve_commit(base_ref)
        boundary_commit = await resolve_commit(boundary_ref)
        if not base_commit or not boundary_commit:
            continue

        code, _ = await git(
            "merge-base", "--is-ancestor", base_commit, boundary_commit
        )
        if code:
            continue
        code, _ = await git(
            "merge-base", "--is-ancestor", boundary_commit, merged_commit
        )
        contained = code == 0
        if not contained:
            if not landing_parent or not merged_tree:
                continue
            code, _ = await git(
                "merge-base", "--is-ancestor", base_commit, landing_parent
            )
            if code:
                continue
            code, fork_out = await git(
                "merge-base", boundary_commit, landing_parent
            )
            fork_lines = fork_out.decode("ascii", errors="ignore").splitlines()
            if (
                    code
                    or len(fork_lines) != 1
                    or fork_lines[0].strip() != base_commit
            ):
                continue
            code, candidate_out = await git(
                "diff", "--name-only", "-z", "--no-renames",
                base_commit, boundary_commit,
            )
            if code:
                continue
            candidate_paths = {
                path for path in candidate_out.split(b"\0") if path
            }
            if not candidate_paths:
                continue
            code, base_out = await git(
                "diff", "--name-only", "-z", "--no-renames",
                base_commit, landing_parent,
            )
            if code:
                continue
            base_paths = {path for path in base_out.split(b"\0") if path}
            if candidate_paths & base_paths:
                continue
            code, merge_tree_out = await git(
                "merge-tree", "--write-tree", merged_commit, boundary_commit
            )
            merge_tree_lines = merge_tree_out.decode(
                "ascii", errors="ignore"
            ).splitlines()
            contained = (
                code == 0
                and len(merge_tree_lines) == 1
                and merge_tree_lines[0].strip() == merged_tree
            )
        if contained:
            task_id = _clean_text(getattr(task, "id", ""))
            if task_id:
                verified.append(task_id)
    return tuple(verified)


def mark_branch_boundaries_merged(tasks: Iterable, *,
                                  repo_root: str,
                                  branch: str,
                                  merge_sha: str,
                                  task_ids: Iterable[str] | str,
                                  merged_at: str | None = None,
                                  pr_metadata: dict | None = None,
                                  requested_cleanup: dict | None = None,
                                  ) -> list:
    """Mark the explicitly attributed task boundaries as merged.

    A worktree branch can be shared by a worker's paused and active tasks, so
    branch identity is only a consistency check, never merge attribution.
    The pipeline root for an attributed derived task is projected as before;
    queued and otherwise branch-matching tasks are left untouched.
    """
    target_ids = {
        _clean_text(task_id)
        for task_id in _normalize_boundary_task_ids(task_ids)
        if _clean_text(task_id)
    }
    if not repo_root or not branch or not target_ids:
        return []

    tasks = list(tasks)
    tasks_by_id = {
        getattr(task, "id", ""): task
        for task in tasks
        if getattr(task, "id", "")
    }
    merged_at = merged_at or datetime.now(timezone.utc).isoformat()
    updated = []
    updated_ids: set[str] = set()
    boundary_sources_by_root: dict[str, object] = {}

    def _append_merged_label(task) -> None:
        labels = list(getattr(task, "labels", []) or [])
        if "merged" not in labels:
            labels.append("merged")
            task.labels = labels

    def _mark_task_boundary_merged(task) -> None:
        boundary = dict(task_boundary(task))
        boundary["status"] = "merged"
        boundary["merged_at"] = merged_at
        boundary["merge_commit_sha"] = merge_sha
        boundary["superseded_by_task_id"] = ""
        boundary.pop("reason", None)
        existing_pr = boundary_pr_metadata(boundary)
        if existing_pr or pr_metadata:
            merged_pr = dict(pr_metadata or {})
            merged_pr.update({
                "state": "merged",
                "merged_at": merged_at,
                "merge_commit_sha": merge_sha,
                "updated_at": merged_at,
            })
            boundary = attach_pr_metadata_to_boundary(
                boundary,
                merged_pr,
                requested_cleanup=requested_cleanup,
                now=merged_at,
            )
        task.worktree_boundary = boundary
        _append_merged_label(task)

    for task in branch_boundary_tasks(
            tasks,
            repo_root=repo_root,
            branch=branch,
            statuses={"open", "superseded"}):
        if _clean_text(getattr(task, "id", "")) not in target_ids:
            continue
        _mark_task_boundary_merged(task)
        updated.append(task)
        task_id = str(getattr(task, "id", "") or "").strip()
        if task_id:
            updated_ids.add(task_id)

        root_id = _task_root_id(task, tasks_by_id)
        if not root_id or root_id == task_id:
            continue
        current_source = boundary_sources_by_root.get(root_id)
        if current_source is None or boundary_sort_key(task) >= boundary_sort_key(
            current_source
        ):
            boundary_sources_by_root[root_id] = task

    for root_id, source_task in boundary_sources_by_root.items():
        if root_id in updated_ids:
            continue
        root_task = tasks_by_id.get(root_id)
        if not root_task:
            continue

        source_boundary = dict(task_boundary(source_task))
        if not source_boundary:
            continue

        boundary = dict(task_boundary(root_task))
        if not boundary:
            boundary = {
                "version": source_boundary.get("version", "1"),
                "repo_root": repo_root,
                "branch": branch,
                "base_branch": source_boundary.get("base_branch", "") or "",
                "commit_sha": source_boundary.get("commit_sha", "") or "",
                "kind": source_boundary.get("kind", "") or "",
                "status": "",
                "recorded_at": _clean_text(
                    source_boundary.get("recorded_at", "")
                ),
                "recorded_by_agent_id": (
                    source_boundary.get("recorded_by_agent_id", "") or ""
                ),
                # Actor/time are projected provenance; contextual completion
                # or review prose is intentionally not projected.
                "message": "",
                "superseded_by_task_id": "",
                "merged_at": "",
                "merge_commit_sha": "",
            }
            if source_boundary.get("submodules"):
                boundary["submodules"] = list(source_boundary.get("submodules") or [])
            if isinstance(source_boundary.get("code_delta"), dict):
                boundary["code_delta"] = dict(source_boundary["code_delta"])
        else:
            if not boundary.get("version"):
                boundary["version"] = source_boundary.get("version", "1") or "1"
            boundary["repo_root"] = repo_root
            boundary["branch"] = branch
            if not boundary.get("base_branch"):
                boundary["base_branch"] = (
                    source_boundary.get("base_branch", "") or ""
                )
            if not boundary.get("commit_sha"):
                boundary["commit_sha"] = (
                    source_boundary.get("commit_sha", "") or ""
                )
            if not boundary.get("kind"):
                boundary["kind"] = source_boundary.get("kind", "") or ""
            if not boundary.get("recorded_by_agent_id"):
                boundary["recorded_by_agent_id"] = (
                    source_boundary.get("recorded_by_agent_id", "") or ""
                )
            boundary.setdefault("message", "")
            if source_boundary.get("submodules") and not boundary.get("submodules"):
                boundary["submodules"] = list(source_boundary.get("submodules") or [])
            # The merged projection represents this source boundary, so carry
            # its classifier even when an older root boundary already exists.
            if isinstance(source_boundary.get("code_delta"), dict):
                boundary["code_delta"] = dict(source_boundary["code_delta"])

        boundary["status"] = "merged"
        boundary["merged_at"] = merged_at
        boundary["merge_commit_sha"] = merge_sha
        boundary["superseded_by_task_id"] = ""
        boundary.pop("reason", None)
        existing_pr = boundary_pr_metadata(boundary)
        if existing_pr or pr_metadata:
            merged_pr = dict(pr_metadata or {})
            merged_pr.update({
                "state": "merged",
                "merged_at": merged_at,
                "merge_commit_sha": merge_sha,
                "updated_at": merged_at,
            })
            boundary = attach_pr_metadata_to_boundary(
                boundary,
                merged_pr,
                requested_cleanup=requested_cleanup,
                now=merged_at,
            )
        root_task.worktree_boundary = boundary
        _append_merged_label(root_task)
        updated.append(root_task)
        updated_ids.add(root_id)

    return updated
