"""Read-only daemon deploy-state helpers for architect observability."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 5
_TORQUE_TASK_ID_RE = re.compile(r"\bTORQUE:\d+\b")
_SOURCE_REPO_ROOT_FILE = ".torque_source_repo_root"


def _run_git(repo_root: str | Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    return proc.stdout.strip()


def _error_text(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        detail = (exc.stderr or exc.stdout or "").strip()
        return detail or f"git exited with status {exc.returncode}"
    return str(exc) or exc.__class__.__name__


def _dedupe_torque_task_ids(text: str) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for task_id in _TORQUE_TASK_ID_RE.findall(text or ""):
        if task_id in seen:
            continue
        seen.add(task_id)
        ids.append(task_id)
    return ids


def _read_source_repo_root_metadata(script_dir: str | Path) -> str:
    try:
        raw = (Path(script_dir) / _SOURCE_REPO_ROOT_FILE).read_text(
            encoding="utf-8"
        )
    except OSError:
        return ""
    lines = raw.splitlines()
    return lines[0].strip() if lines else ""


def _deploy_repo_candidates(state, repo_hint: str | Path) -> list[str]:
    candidates = [
        os.environ.get("TORQUE_DEPLOY_REPO_ROOT", "").strip(),
        _read_source_repo_root_metadata(repo_hint),
        str(repo_hint or "").strip(),
    ]
    for cell in getattr(state, "agents", {}).values():
        candidates.extend([
            getattr(cell, "worktree_repo_root", ""),
            getattr(cell, "git_root", ""),
            getattr(cell, "directory", ""),
        ])

    seen: set[str] = set()
    deduped: list[str] = []
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        key = os.path.expanduser(text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def capture_deploy_boot_state(state, repo_hint: str | Path) -> None:
    """Record daemon boot git state in memory without raising on failure."""
    if not getattr(state, "boot_timestamp", 0):
        state.boot_timestamp = time.time()
    state.boot_repo_root = ""
    state.boot_head_commit = ""
    state.boot_mainline_branch = ""
    state.boot_head_error = ""

    errors: list[str] = []
    for candidate in _deploy_repo_candidates(state, repo_hint):
        try:
            repo_root = _run_git(candidate, "rev-parse", "--show-toplevel")
            head = _run_git(repo_root, "rev-parse", "HEAD")
            branch = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        except Exception as exc:
            errors.append(f"{candidate}: {_error_text(exc)}")
            continue

        state.boot_repo_root = repo_root
        state.boot_head_commit = head
        if branch == "HEAD":
            state.boot_head_error = "repository is in detached HEAD state"
            return
        state.boot_mainline_branch = branch
        return

    detail = "; ".join(errors[:3])
    suffix = f": {detail}" if detail else ""
    state.boot_head_error = f"failed to capture boot git state{suffix}"


def _group_mainline_branch(state, group: str, current_branch: str) -> str:
    group_settings = getattr(state, "group_settings", {}).get(str(group or ""))
    configured = str(
        getattr(group_settings, "worktree_base_branch", "") or ""
    ).strip()
    if configured:
        return configured
    return (
        str(getattr(state, "boot_mainline_branch", "") or "").strip()
        or str(current_branch or "").strip()
        or "main"
    )


def architect_deploy_state_payload(state, group: str, *, now: float | None = None) -> dict:
    """Return pending-deploy state, failing gracefully on git errors."""
    timestamp = float(getattr(state, "boot_timestamp", 0) or 0)
    now_ts = float(time.time() if now is None else now)
    boot_head = str(getattr(state, "boot_head_commit", "") or "").strip()
    repo_root = str(getattr(state, "boot_repo_root", "") or "").strip()
    payload = {
        "boot_timestamp": timestamp,
        "boot_head_commit": boot_head,
        "current_head_commit": "",
        "pending_deploy": {
            "count": -1,
            "torque_task_ids": [],
        },
        "daemon_uptime_seconds": max(0, int(now_ts - timestamp)) if timestamp else 0,
    }

    def fail(message: str) -> dict:
        payload["error"] = str(message or "unknown deploy-state error")
        return payload

    boot_error = str(getattr(state, "boot_head_error", "") or "").strip()
    if boot_error:
        return fail(boot_error)
    if not repo_root:
        return fail("boot repo root is unavailable")
    if not boot_head:
        return fail("boot HEAD commit is unavailable")

    try:
        current_branch = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        if current_branch == "HEAD":
            return fail("repository is in detached HEAD state")
        mainline = _group_mainline_branch(state, group, current_branch)
        current_head = _run_git(repo_root, "rev-parse", "HEAD")
        payload["current_head_commit"] = current_head
        revision_range = f"{boot_head}..{mainline}"
        count_text = _run_git(repo_root, "rev-list", "--count", revision_range)
        log_text = _run_git(
            repo_root,
            "log",
            "--format=%B",
            "--reverse",
            revision_range,
        )
    except Exception as exc:
        return fail(f"failed to read deploy git state: {_error_text(exc)}")

    try:
        count = int(count_text or "0")
    except ValueError:
        return fail(f"invalid git rev-list count: {count_text!r}")

    payload["pending_deploy"] = {
        "count": count,
        "torque_task_ids": _dedupe_torque_task_ids(log_text),
    }
    return payload
