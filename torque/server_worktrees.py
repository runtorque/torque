"""Worktree diff, review, and merge helper functions for the server."""

from __future__ import annotations

import asyncio
import re
import shlex

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .config import log
from .board_sync.github import issue_ref_for_closing, parse_github_issue_ref
from .state import task_counts_as_done
from .worktree_boundaries import (
    attach_pr_metadata_to_boundary,
    attach_pr_metadata_to_latest_open_boundary,
    branch_boundary_tasks,
    task_boundary,
)


_TORQUE_TASK_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_:/-])"
    r"(?P<task_id>[A-Z][A-Z0-9_]*:[1-9][0-9]*(?::[1-9][0-9]*)?)"
    r"(?![A-Za-z0-9_:/-])"
)
_MARKDOWN_FENCE_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")
_INLINE_CODE_TICKS_RE = re.compile(r"`+")


@dataclass
class WorktreeCommandTarget:
    """Value object for live-agent or driverless worktree operations."""

    id: str
    name: str
    group: str
    worktree_path: str
    worktree_branch: str
    worktree_repo_root: str
    worktree_base_branch: str
    git_root: str = ""
    slug: str = ""
    worktree_merge_squash: bool = True
    worktree_checkpoints: int = 0
    worktree_dirty: bool = False
    worktree_diff: dict | None = None
    worktree_changed_files: list | None = None
    worktree_ahead: int = 0
    worktree_behind: int = 0
    worktree_merged: bool = False
    current_task_id: str = ""
    source_agent_id: str = ""
    driverless: bool = False
    cell: object | None = None


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
        cells = [
            cell for cell in state.iter_active_agents()
            if getattr(cell, "worktree_path", "")
        ]

        async def _refresh_cell(cell):
            try:
                gs = state.get_group_settings(getattr(cell, "group", "") or "")
                cell_changed = await worktree_mgr.refresh_state(
                    cell,
                    worktree_submodules=getattr(
                        gs,
                        "worktree_submodules",
                        [],
                    ),
                )
            except Exception:
                log.exception(
                    "Worktree refresh failed for '%s'", cell.name)
                return False
            if cell_changed:
                state._emit_agent(cell)
            return bool(cell_changed)

        results = await asyncio.gather(
            *(_refresh_cell(cell) for cell in cells),
            return_exceptions=True,
        )
        changed = any(result is True for result in results)
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
    body_lines = lines[1:]
    if _is_generic_merge_title(title):
        title = _pr_title_from_body_lines(body_lines) or fallback_title
    body = "\n".join(body_lines).strip()
    return title, body


def _is_generic_merge_title(title: str) -> bool:
    """Return true for git/GitHub-generated merge subjects."""
    title = str(title or "").strip()
    return (
        title.startswith("Squash merge: ")
        or title.startswith("Merge branch ")
    )


def _pr_title_from_body_lines(lines: list[str]) -> str:
    """Derive a concise PR title from generated merge-message body lines."""
    for line in lines:
        candidate = str(line or "").strip()
        if not candidate:
            continue
        if candidate.startswith(("- ", "* ")):
            candidate = candidate[2:].strip()
        if candidate and not _is_generic_merge_title(candidate):
            return candidate
    return ""


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


def _resolve_board_task_for_pr_ref(state, raw_task_id: str):
    """Resolve an exact canonical task token to a live board task."""
    raw_task_id = str(raw_task_id or "").strip()
    if not state or not raw_task_id:
        return None
    resolver = getattr(state, "resolve_board_task_id", None)
    if callable(resolver):
        try:
            resolved_id = resolver(raw_task_id, allow_prefix=False)
        except TypeError:
            resolved_id = resolver(raw_task_id)
    else:
        resolve_alias = getattr(state, "resolve_task_alias", None)
        resolved_id = (
            resolve_alias(raw_task_id)
            if callable(resolve_alias)
            else raw_task_id
        )
        if resolved_id != raw_task_id:
            if resolved_id not in getattr(state, "board_tasks", {}):
                resolved_id = ""
        elif raw_task_id not in getattr(state, "board_tasks", {}):
            resolved_id = ""
    if not resolved_id:
        return None
    return getattr(state, "board_tasks", {}).get(resolved_id)


def _resolve_task_ref_to_github_issue(state, raw_task_id: str) -> dict | None:
    """Resolve a Torque task token to its synced GitHub issue metadata."""
    task = _resolve_board_task_for_pr_ref(state, raw_task_id)
    if not task:
        return None

    external_id = str(getattr(task, "external_id", "") or "").strip()
    external_url = str(getattr(task, "external_url", "") or "").strip()
    board_sync = getattr(task, "board_sync", {}) or {}
    if not isinstance(board_sync, dict):
        board_sync = {}
    parsed = parse_github_issue_ref(
        external_id=external_id,
        external_url=external_url,
        board_sync=board_sync,
    )

    # ``parse_github_issue_ref`` prioritizes board_sync.github.issue_repo and
    # issue_number.  If a future/partial sync stores only issue_url there, use
    # the same parser against that URL before giving up.
    github_sync = (
        board_sync.get("github")
        if isinstance(board_sync.get("github"), dict)
        else {}
    )
    if (
            (not parsed.get("issue_repo") or not parsed.get("issue_number"))
            and str(github_sync.get("issue_url", "") or "").strip()):
        parsed = parse_github_issue_ref(
            external_url=str(github_sync.get("issue_url", "") or "").strip()
        )

    repo = str(parsed.get("issue_repo", "") or "").strip()
    number = parsed.get("issue_number")
    try:
        number = int(number or 0)
    except (TypeError, ValueError):
        number = 0
    issue_url = str(parsed.get("issue_url", "") or "").strip()
    if not ((repo and number) or issue_url):
        return None

    return {
        "provider": "github",
        "task_id": str(getattr(task, "id", "") or "").strip(),
        "task_title": str(getattr(task, "task", "") or "").strip(),
        "external_id": external_id,
        "external_url": external_url,
        "board_sync": board_sync,
        "issue_repo": repo,
        "issue_number": number,
        "issue_url": issue_url,
    }


def _github_issue_ref_for_pr_text(issue: dict, *, base_repo: str = "") -> str:
    """Render the concise GitHub issue ref used inside PR title/body text."""
    issue = dict(issue or {})
    issue["base_repo"] = str(base_repo or "").strip()
    ref = issue_ref_for_closing(issue, default_repo=base_repo)
    if ref:
        return ref
    return str(issue.get("issue_url", "") or "").strip()


def _replace_task_refs_in_plain_markdown_text(
    text: str,
    replace_fn,
) -> tuple[str, int]:
    """Rewrite task refs in text that is known not to contain markdown code."""
    skipped_inline_refs = 0
    result: list[str] = []
    pos = 0
    for match in _INLINE_CODE_TICKS_RE.finditer(text):
        if match.start() < pos:
            continue
        ticks = match.group(0)
        result.append(_TORQUE_TASK_REF_RE.sub(replace_fn, text[pos:match.start()]))
        close = text.find(ticks, match.end())
        if close < 0:
            code_span = text[match.start():]
            skipped_inline_refs += len(_TORQUE_TASK_REF_RE.findall(code_span))
            result.append(code_span)
            pos = len(text)
            break
        code_span = text[match.start():close + len(ticks)]
        skipped_inline_refs += len(_TORQUE_TASK_REF_RE.findall(code_span))
        result.append(code_span)
        pos = close + len(ticks)
    if pos < len(text):
        result.append(_TORQUE_TASK_REF_RE.sub(replace_fn, text[pos:]))
    return "".join(result), skipped_inline_refs


def _rewrite_markdown_task_refs_outside_code(
    text: str,
    replace_fn,
) -> tuple[str, dict]:
    """Apply ``replace_fn`` to task refs outside fenced and inline code."""
    text = str(text or "")
    diagnostics = {"skipped_fenced_refs": 0, "skipped_inline_refs": 0}
    output: list[str] = []
    pending_plain: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    def flush_plain() -> None:
        nonlocal pending_plain
        if not pending_plain:
            return
        rewritten, skipped_inline = _replace_task_refs_in_plain_markdown_text(
            "".join(pending_plain),
            replace_fn,
        )
        diagnostics["skipped_inline_refs"] += skipped_inline
        output.append(rewritten)
        pending_plain = []

    for line in text.splitlines(keepends=True):
        fence_match = _MARKDOWN_FENCE_RE.match(line)
        if in_fence:
            diagnostics["skipped_fenced_refs"] += len(
                _TORQUE_TASK_REF_RE.findall(line)
            )
            output.append(line)
            if fence_match:
                fence = fence_match.group("fence")
                if fence.startswith(fence_char) and len(fence) >= fence_len:
                    in_fence = False
                    fence_char = ""
                    fence_len = 0
            continue

        if fence_match:
            flush_plain()
            fence = fence_match.group("fence")
            in_fence = True
            fence_char = fence[0]
            fence_len = len(fence)
            diagnostics["skipped_fenced_refs"] += len(
                _TORQUE_TASK_REF_RE.findall(line)
            )
            output.append(line)
            continue

        pending_plain.append(line)

    flush_plain()
    return "".join(output), diagnostics


def _rewrite_pr_torque_task_refs(
    text: str,
    *,
    state,
    base_repo: str = "",
) -> tuple[str, dict]:
    """Rewrite Torque task refs in PR text to synced GitHub issue refs."""
    diagnostics = {
        "replaced": [],
        "unresolved": [],
        "skipped_fenced_refs": 0,
        "skipped_inline_refs": 0,
    }
    resolved_cache: dict[str, tuple[str, dict | None]] = {}

    def replacement(match: re.Match) -> str:
        raw_task_id = match.group("task_id")
        if raw_task_id not in resolved_cache:
            issue = _resolve_task_ref_to_github_issue(state, raw_task_id)
            ref = (
                _github_issue_ref_for_pr_text(issue, base_repo=base_repo)
                if issue
                else ""
            )
            resolved_cache[raw_task_id] = (ref, issue)
        ref, issue = resolved_cache[raw_task_id]
        if not ref or not issue:
            diagnostics["unresolved"].append({"task_id": raw_task_id})
            return match.group(0)
        diagnostics["replaced"].append({
            "task_id": str(issue.get("task_id") or raw_task_id),
            "raw_task_id": raw_task_id,
            "ref": ref,
            "issue_repo": str(issue.get("issue_repo", "") or "").strip(),
            "issue_number": int(issue.get("issue_number") or 0),
            "issue_url": str(issue.get("issue_url", "") or "").strip(),
        })
        return ref

    rewritten, markdown_diagnostics = _rewrite_markdown_task_refs_outside_code(
        str(text or ""),
        replacement,
    )
    diagnostics["skipped_fenced_refs"] += int(
        markdown_diagnostics.get("skipped_fenced_refs", 0) or 0
    )
    diagnostics["skipped_inline_refs"] += int(
        markdown_diagnostics.get("skipped_inline_refs", 0) or 0
    )
    return rewritten, diagnostics


def _rewrite_pr_torque_task_refs_metadata(
    title: str,
    body: str,
    *,
    state,
    base_repo: str = "",
) -> dict:
    """Rewrite Torque task refs in PR title/body metadata."""
    rewritten_title, title_diagnostics = _rewrite_pr_torque_task_refs(
        title,
        state=state,
        base_repo=base_repo,
    )
    rewritten_body, body_diagnostics = _rewrite_pr_torque_task_refs(
        body,
        state=state,
        base_repo=base_repo,
    )
    return {
        "title": rewritten_title,
        "body": rewritten_body,
        "replaced": (
            title_diagnostics.get("replaced", [])
            + body_diagnostics.get("replaced", [])
        ),
        "unresolved": (
            title_diagnostics.get("unresolved", [])
            + body_diagnostics.get("unresolved", [])
        ),
        "skipped_fenced_refs": (
            int(title_diagnostics.get("skipped_fenced_refs", 0) or 0)
            + int(body_diagnostics.get("skipped_fenced_refs", 0) or 0)
        ),
        "skipped_inline_refs": (
            int(title_diagnostics.get("skipped_inline_refs", 0) or 0)
            + int(body_diagnostics.get("skipped_inline_refs", 0) or 0)
        ),
        "title_diagnostics": title_diagnostics,
        "body_diagnostics": body_diagnostics,
    }


def _github_issue_from_linked_task(task, *, base_repo: str = "") -> dict | None:
    """Return GitHub issue metadata for a linked Torque task, if any."""
    external_id = str(getattr(task, "external_id", "") or "").strip()
    external_url = str(getattr(task, "external_url", "") or "").strip()
    board_sync = getattr(task, "board_sync", {}) or {}
    board_sync = board_sync if isinstance(board_sync, dict) else {}
    github_sync = (
        board_sync.get("github")
        if isinstance(board_sync.get("github"), dict)
        else {}
    )
    task_provider = str(getattr(task, "provider", "") or "").strip().lower()
    sync_provider = str(board_sync.get("provider", "") or "").strip().lower()
    has_github_sync = bool(github_sync) or sync_provider == "github"
    if task_provider and task_provider != "github" and not has_github_sync:
        return None
    parsed = parse_github_issue_ref(
        external_id=external_id,
        external_url=external_url,
        board_sync=board_sync,
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
        "board_sync": board_sync,
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
    state.emit_task_upsert(latest)
    state._db_save_task(latest)
    return latest.worktree_boundary


def _record_pr_metadata_on_task_boundary(
        state,
        cell,
        task_id: str,
        pr_metadata: dict,
        requested_cleanup: dict | None = None,
        ) -> dict | None:
    """Attach PR state to the merge-attributed task boundary only.

    A worker branch may have several open task boundaries.  PR metadata is
    durable merge evidence, so selecting the newest boundary by branch would
    contaminate a paused task.  Validate the selected task still describes the
    worker's branch before recording anything.
    """
    if not state or not cell or not task_id or not pr_metadata:
        return None
    task = state.board_tasks.get(str(task_id))
    if not task:
        return None
    boundary = task_boundary(task)
    repo_root = str(cell.worktree_repo_root or cell.git_root or "").strip()
    branch = str(cell.worktree_branch or "").strip()
    if (
            not boundary
            or str(boundary.get("repo_root") or "").strip() != repo_root
            or str(boundary.get("branch") or "").strip() != branch):
        return None
    task.worktree_boundary = attach_pr_metadata_to_boundary(
        boundary,
        pr_metadata,
        requested_cleanup=requested_cleanup,
    )
    task.updated_at = datetime.now(timezone.utc).isoformat()
    state.emit_task_upsert(task)
    state._db_save_task(task)
    return task.worktree_boundary


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
