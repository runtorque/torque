"""Worktree diff, review, and merge helper functions for the server."""

from __future__ import annotations

import asyncio
import shlex

from dataclasses import asdict
from datetime import datetime, timezone

from .config import log
from .board_sync.github import parse_github_issue_ref
from .state import task_counts_as_done
from .worktree_boundaries import (
    attach_pr_metadata_to_latest_open_boundary,
    branch_boundary_tasks,
)


def _parse_diff_git_paths(line: str) -> tuple[str, str]:
    """Extract old/new paths from a ``diff --git`` header."""
    try:
        parts = shlex.split(line)
    except ValueError:
        return "", ""
    if len(parts) < 4 or parts[0] != "diff" or parts[1] != "--git":
        return "", ""
    old_path = parts[2][2:] if parts[2].startswith("a/") else parts[2]
    new_path = parts[3][2:] if parts[3].startswith("b/") else parts[3]
    return old_path, new_path


def _finalize_worktree_diff_file(file_info: dict) -> dict:
    """Normalize a parsed diff file record for the frontend."""
    path = file_info.get("new_path") or file_info.get("old_path") or ""
    if file_info.get("status") == "deleted":
        path = file_info.get("old_path") or path
    file_info["path"] = path
    file_info["insertions"] = 0
    file_info["deletions"] = 0
    for hunk in file_info.get("hunks", []):
        for line in hunk.get("lines", []):
            if line["type"] == "add":
                file_info["insertions"] += 1
            elif line["type"] == "del":
                file_info["deletions"] += 1
    return {
        "path": file_info["path"],
        "status": file_info.get("status") or "modified",
        "insertions": file_info["insertions"],
        "deletions": file_info["deletions"],
        "binary": bool(file_info.get("binary")),
        "hunks": file_info.get("hunks", []),
    }


def _parse_unified_diff(diff_text: str) -> list[dict]:
    """Parse ``git diff`` unified output into structured file hunks."""
    files: list[dict] = []
    current_file = None
    current_hunk = None

    def finish_current():
        nonlocal current_file, current_hunk
        if not current_file:
            return
        if current_hunk:
            current_file["hunks"].append(current_hunk)
        files.append(_finalize_worktree_diff_file(current_file))
        current_file = None
        current_hunk = None

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            finish_current()
            old_path, new_path = _parse_diff_git_paths(raw_line)
            status = "renamed" if old_path and new_path and old_path != new_path else "modified"
            current_file = {
                "old_path": old_path,
                "new_path": new_path,
                "status": status,
                "binary": False,
                "hunks": [],
            }
            continue
        if current_file is None:
            continue
        if raw_line.startswith("new file mode "):
            current_file["status"] = "added"
            continue
        if raw_line.startswith("deleted file mode "):
            current_file["status"] = "deleted"
            continue
        if raw_line.startswith("rename from "):
            current_file["old_path"] = raw_line[len("rename from "):]
            current_file["status"] = "renamed"
            continue
        if raw_line.startswith("rename to "):
            current_file["new_path"] = raw_line[len("rename to "):]
            current_file["status"] = "renamed"
            continue
        if raw_line.startswith("copy from "):
            current_file["old_path"] = raw_line[len("copy from "):]
            current_file["status"] = "copied"
            continue
        if raw_line.startswith("copy to "):
            current_file["new_path"] = raw_line[len("copy to "):]
            current_file["status"] = "copied"
            continue
        if raw_line.startswith("Binary files ") or raw_line == "GIT binary patch":
            current_file["binary"] = True
            current_hunk = None
            continue
        if raw_line.startswith("@@ "):
            if current_hunk:
                current_file["hunks"].append(current_hunk)
            current_hunk = {"header": raw_line, "lines": []}
            continue
        if not current_hunk:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++ "):
            current_hunk["lines"].append({"type": "add", "text": raw_line[1:]})
        elif raw_line.startswith("-") and not raw_line.startswith("--- "):
            current_hunk["lines"].append({"type": "del", "text": raw_line[1:]})
        elif raw_line.startswith(" "):
            current_hunk["lines"].append({"type": "context", "text": raw_line[1:]})
        elif raw_line.startswith("\\ No newline at end of file"):
            current_hunk["lines"].append({"type": "context", "text": raw_line})

    finish_current()
    return files


async def _worktree_diff_updater(state, worktree_mgr):
    """Periodically update diff stats for cells with active worktrees."""
    while True:
        await asyncio.sleep(60)
        changed = False
        for cell in state.iter_active_agents():
            if not cell.worktree_path:
                continue
            try:
                cell_changed = await worktree_mgr.refresh_state(cell)
            except Exception:
                log.exception(
                    "Worktree refresh failed for '%s'", cell.name)
                continue
            if cell_changed:
                state._emit_agent(cell)
                changed = True
        if changed:
            await state.broadcast()


async def _worktree_merge_diff_snapshot(cell, worktree_mgr) -> dict:
    """Capture the full pre-merge patch plus structured summary data."""
    if not cell or not cell.worktree_path:
        return {"error": "Agent has no worktree."}
    base_branch = cell.worktree_base_branch or "main"
    stale_base = {}
    try:
        stale_base = await worktree_mgr.stale_base_info(cell)
    except AttributeError:
        stale_base = {}
    try:
        stats = await worktree_mgr.diff_summary(cell)
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", cell.worktree_path,
            "diff", "--no-color", "--find-renames", "--binary", "--unified=3",
            f"{base_branch}...HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode().strip() or "Failed to load worktree diff."
            return {"error": err}
        patch_text = stdout.decode()
        files = _parse_unified_diff(patch_text)
        if not stats:
            stats = {
                "files": len(files),
                "insertions": sum(f.get("insertions", 0) for f in files),
                "deletions": sum(f.get("deletions", 0) for f in files),
            }
        return {
            "agent_name": cell.name,
            "branch": cell.worktree_branch or "",
            "base_branch": base_branch,
            "stats": stats,
            "files": files,
            "patch_text": patch_text,
            **({
                "stale_base": stale_base,
                "stale_base_warning": stale_base.get("warning", ""),
            } if stale_base.get("stale") else {}),
        }
    except Exception:
        log.exception("Failed to build merge diff snapshot for '%s'", cell.name)
        return {"error": "Failed to load worktree diff."}


async def _worktree_full_diff(cell, worktree_mgr) -> dict:
    """Build the structured diff payload for the worktree review view."""
    snapshot = await _worktree_merge_diff_snapshot(cell, worktree_mgr)
    if snapshot.get("error"):
        return {"error": snapshot["error"]}
    snapshot.pop("patch_text", None)
    return snapshot


async def _generate_merge_message(cell, worktree_mgr, squash: bool,
                                  state=None) -> str:
    """Build a default merge commit message from completed tasks and commits."""
    branch = cell.worktree_branch or cell.name
    header = f"Squash merge: {branch}" if squash else f"Merge branch '{branch}'"

    task_lines = []
    if state:
        branch_tasks = branch_boundary_tasks(
            state.board_tasks.values(),
            repo_root=cell.worktree_repo_root or cell.git_root or "",
            branch=cell.worktree_branch,
            statuses={"open", "superseded"},
        )
        for task in branch_tasks:
            if not task_counts_as_done(task):
                continue
            done_msg = ""
            for message in reversed(task.messages):
                if message.get("action") in {"done", "ready"} and message.get("message"):
                    done_msg = message["message"]
                    break
            line = f"- {task.task}"
            if done_msg and done_msg != "Done":
                line += f"\n  {done_msg}"
            task_lines.append(line)

    if task_lines:
        return header + "\n\n" + "\n".join(task_lines)

    commits = await worktree_mgr.list_checkpoints(cell)
    if commits:
        lines = [header, ""]
        for commit in commits:
            message = commit["message"]
            if message.startswith("torque: checkpoint") or message.startswith("torque: task boundary"):
                body = commit.get("body", "").strip()
                if body:
                    lines.append(f"- {body.splitlines()[0]}")
                continue
            lines.append(f"- {message}")
        if len(lines) > 2:
            return "\n".join(lines)

    return header


def _split_merge_message_for_pr(message: str, *,
                                fallback_title: str) -> tuple[str, str]:
    """Split a generated merge message into GitHub PR title/body parts."""
    lines = str(message or "").strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return fallback_title, ""
    title = lines[0].strip() or fallback_title
    body = "\n".join(lines[1:]).strip()
    return title, body


def _append_pr_url_to_squash_body(body: str, pr_url: str) -> str:
    """Append the PR URL to the squash commit body without duplicating it."""
    body = str(body or "").strip()
    pr_url = str(pr_url or "").strip()
    if not pr_url or pr_url in body:
        return body
    suffix = f"PR: {pr_url}"
    if body:
        return f"{body}\n\n{suffix}"
    return suffix


def _github_issue_from_linked_task(task, *, base_repo: str = "") -> dict | None:
    """Return GitHub issue metadata for a linked Torque task, if any."""
    if str(getattr(task, "provider", "") or "").strip().lower() != "github":
        return None

    external_id = str(getattr(task, "external_id", "") or "").strip()
    external_url = str(getattr(task, "external_url", "") or "").strip()
    board_sync = getattr(task, "board_sync", {}) or {}
    parsed = parse_github_issue_ref(
        external_id=external_id,
        external_url=external_url,
        board_sync=board_sync if isinstance(board_sync, dict) else {},
    )
    repo = str(parsed.get("issue_repo", "") or "").strip()
    number = parsed.get("issue_number")
    try:
        number = int(number or 0)
    except (TypeError, ValueError):
        number = 0
    if not repo or not number:
        return None

    return {
        "provider": "github",
        "task_id": str(getattr(task, "id", "") or "").strip(),
        "task_title": str(getattr(task, "task", "") or "").strip(),
        "external_id": external_id,
        "external_url": external_url,
        "board_sync": board_sync if isinstance(board_sync, dict) else {},
        "issue_repo": repo,
        "issue_number": number,
        "issue_url": str(parsed.get("issue_url", "") or "").strip(),
        "base_repo": str(base_repo or "").strip(),
    }


def _collect_linked_github_issues(tasks, *, base_repo: str = "") -> list[dict]:
    """Collect de-duplicated GitHub issues linked from the supplied tasks."""
    issues: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for task in tasks or []:
        issue = _github_issue_from_linked_task(task, base_repo=base_repo)
        if not issue:
            continue
        key = (
            str(issue.get("issue_repo", "") or "").strip().lower(),
            int(issue.get("issue_number") or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        issues.append(issue)
    return issues


def _pr_result_metadata(
    *,
    pr_result: dict | None = None,
    merge_result: dict | None = None,
    remote: str = "",
    base_branch: str = "",
    branch: str = "",
    pending: bool = False,
    status: str = "",
) -> dict:
    """Normalize GitHub PR helper output for command results/boundaries."""
    pr_result = pr_result or {}
    merge_result = merge_result or {}
    pr_status = merge_result.get("pr_status")
    if isinstance(pr_status, dict) and pr_status.get("ok"):
        source = {**pr_result, **pr_status, **merge_result}
    else:
        source = {**pr_result, **merge_result}
    url = str(source.get("url") or pr_result.get("url") or "").strip()
    number = source.get("number", pr_result.get("number"))
    head_sha = str(
        source.get("head_sha") or pr_result.get("head_sha") or ""
    ).strip()
    metadata = {
        "url": url,
        "number": number,
        "head_sha": head_sha,
        "base_branch": str(base_branch or "").strip(),
        "head_branch": str(branch or "").strip(),
        "remote": str(remote or "").strip(),
        "state": str(
            source.get("state") or pr_result.get("state") or ""
        ).strip(),
        "merge_state": str(
            source.get("merge_state") or pr_result.get("merge_state") or ""
        ).strip(),
        "merge_commit_sha": str(
            source.get("merge_commit_sha")
            or pr_result.get("merge_commit_sha")
            or ""
        ).strip(),
        "pending": bool(pending),
        "status": str(status or "").strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if "existing" in pr_result:
        metadata["existing"] = bool(pr_result.get("existing"))
    if source.get("review_decision") is not None:
        metadata["review_decision"] = source.get("review_decision")
    return metadata


def _record_pr_metadata_on_latest_boundary(state, cell,
                                           pr_metadata: dict,
                                           requested_cleanup: dict | None = None,
                                           ) -> dict | None:
    """Attach PR state to the latest open branch boundary without closing it."""
    if not state or not cell or not pr_metadata:
        return None
    repo_root = cell.worktree_repo_root or cell.git_root or ""
    latest = attach_pr_metadata_to_latest_open_boundary(
        state.board_tasks.values(),
        repo_root=repo_root,
        branch=cell.worktree_branch or "",
        pr_metadata=pr_metadata,
        requested_cleanup=requested_cleanup,
    )
    if not latest:
        return None

    latest.updated_at = datetime.now(timezone.utc).isoformat()
    state._emit("task_upsert", **asdict(latest))
    state._db_save_task(latest)
    return latest.worktree_boundary


def _pr_merge_failure_allows_auto(merge_result: dict) -> bool:
    """Return true when an immediate PR merge failure can fall back to --auto."""
    if not isinstance(merge_result, dict) or merge_result.get("ok"):
        return False
    if merge_result.get("pending"):
        return False
    status = merge_result.get("pr_status")
    if not isinstance(status, dict):
        status = {}
    state_text = str(
        merge_result.get("state") or status.get("state") or ""
    ).upper()
    merge_state = str(
        merge_result.get("merge_state") or status.get("merge_state") or ""
    ).upper()
    if state_text and state_text != "OPEN":
        return False
    if merge_state in {"DIRTY", "UNKNOWN"}:
        return False
    # GitHub uses a few different merge-state labels for protected branches
    # and pending checks/reviews. If the PR is still open and not dirty, asking
    # GitHub to enable auto-merge is the safest default follow-up.
    return True
