"""Git worktree lifecycle management for Torque agents."""

import asyncio
import contextlib
import glob
import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Optional

from torque.config import log

# Files Torque injects into user repos (adapters, MCP, hooks, skills).
# These must be excluded from git so they don't pollute `git status`.
TORQUE_EXCLUDE_ENTRIES = [
    ".mcp.json",
    ".claude/settings.local.json",
    ".claude/instructions.md",
    ".claude/skills/torque-*/",
    ".codex/config.toml",
    ".codex/hooks.json",
    ".codex/AGENTS.md",
    ".torque/claude-auto-memory-original.json",
    ".torque/torque-system-prompt-*.md",
]

@dataclass
class ExistingWorktreeTarget:
    """Validated, non-mutating view of an existing git worktree."""

    repo_root: str
    worktree_path: str
    branch: str
    head_sha: str
    base_branch: str = ""
    git_root: str = ""
    is_dirty: bool = False
    listed_worktree_entry: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


_CLAUDE_CODE_SETTINGS_DIR = ".claude"
_CLAUDE_CODE_SETTINGS_FILE = "settings.local.json"
_CLAUDE_CODE_AUTO_MEMORY_DISABLED_KINDS = {"architect", "engineer", "worker"}
_HIGH_CHURN_THRESHOLD = 200
_LOCKFILE_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "poetry.lock",
    "pipfile.lock",
    "cargo.lock",
    "composer.lock",
    "gemfile.lock",
    "go.sum",
}
_MIGRATION_RE = re.compile(
    r"(^|/)(db/migrate|migrations?|alembic|schema\.sql|prisma/migrations?)(/|$)",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"(^|/)(auth|oauth|login|session|permissions?|rbac|acl)(/|$)|"
    r"(^|/)(auth|oauth|login|session|permissions?|rbac|acl)[._-]",
    re.IGNORECASE,
)
_PROMPT_RE = re.compile(
    r"(^|/)(prompts?|system-prompt|prompting)(/|$)|prompt",
    re.IGNORECASE,
)
_CONFIG_RE = re.compile(
    r"(^|/)(\.github/workflows|config|configs|settings|infra|deploy|docker)(/|$)|"
    r"(^|/)(dockerfile|docker-compose\.[^/]+|compose\.[^/]+|\.env(\.|$)|"
    r"[^/]+\.(ya?ml|toml|ini|cfg))$",
    re.IGNORECASE,
)
_BUILD_TEST_RE = re.compile(
    r"(^|/)(makefile|package\.json|pyproject\.toml|requirements[^/]*\.txt|"
    r"tox\.ini|noxfile\.py|setup\.py|setup\.cfg|cargo\.toml|go\.mod|"
    r"pom\.xml|build\.gradle(\.kts)?|scripts?|ci|tests?)(/|$)|"
    r"(^|/)\.github/workflows/",
    re.IGNORECASE,
)
# Worker-namespaced branches: ``torque/<engineer-slug>/<worker>`` and
# ``torque/user/<worker>`` (a two-level torque branch). Used by the A1
# base-branch safety guard (TORQUE:604) to refuse forking a fresh worktree
# off a repo-root HEAD that was left on another worker's branch.
_WORKER_NAMESPACED_BRANCH_RE = re.compile(r"^torque/[^/]+/.+")
_WORKTREE_NAME_MAX_LEN = 40
# Bounded background refresh probes: each git subprocess in the periodic
# worktree refresh path gets this many seconds before the refresh is treated
# as stale and the previous cell state is preserved. Keep comfortably below
# the 60s refresh cadence so one slow repository cannot monopolize the daemon.
WORKTREE_REFRESH_GIT_TIMEOUT_SECONDS = 10.0
WORKTREE_REFRESH_KILL_GRACE_SECONDS = 1.0
WORKTREE_REFRESH_MAX_CONCURRENT = 4
WORKTREE_REFRESH_LOG_THROTTLE_SECONDS = 300.0
_TEST_DIR_NAMES = {
    "__snapshots__",
    "__tests__",
    "spec",
    "specs",
    "test",
    "tests",
}
_TEST_FILE_RE = re.compile(
    r"(^conftest\.py$|"
    r"^test[_-].+\.[^/]+$|"
    r".+[_-]tests?\.[^/]+$|"
    r".+\.(test|spec)\.[^/]+$|"
    r".+tests?\.(java|kt|kts|scala|cs)$)",
    re.IGNORECASE,
)


def _normalize_repo_rel_path(path: str) -> str:
    normalized = os.path.normpath(str(path or "").strip()).replace(os.sep, "/")
    if normalized in {"", "."}:
        return ""
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _normalize_worktree_submodules(paths) -> list[str]:
    """Return safe, deduped repo-relative submodule paths for worktrees."""
    if not paths:
        return []
    if isinstance(paths, str):
        raw = paths.strip()
        if not raw:
            return []
        parsed = None
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                parsed = None
        if isinstance(parsed, list):
            paths = parsed
        else:
            paths = re.split(r"[\n,]+", raw)

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        raw = str(raw_path or "").strip()
        if not raw or os.path.isabs(raw):
            continue
        path = _normalize_repo_rel_path(raw)
        if not path:
            continue
        parts = [part for part in path.split("/") if part]
        if any(part == ".." for part in parts):
            continue
        if path == ".git" or path.startswith(".git/"):
            continue
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    return normalized


def _ee_pr_flow_submodule_paths(paths) -> list[str]:
    """Return submodule paths handled by the ee PR-first flow."""
    normalized = _normalize_worktree_submodules(paths)
    return [
        path for path in normalized
        if [part for part in path.split("/") if part][-1:] == ["ee"]
    ]


def _legacy_nested_submodule_paths(paths,
                                   pr_flow_submodules: list[str]) -> list[str]:
    """Return configured nested submodules outside the ee PR-first flow."""
    pr_flow_set = set(_normalize_worktree_submodules(pr_flow_submodules))
    return [
        path for path in _normalize_worktree_submodules(paths)
        if path not in pr_flow_set
    ]


def _is_test_path(path: str) -> bool:
    """Return whether *path* looks like test-only coverage.

    This intentionally catches common test directories plus language-idiomatic
    test file suffixes. It is used for review-gate LOC accounting, not for
    deciding whether tests should be ignored elsewhere in Torque.
    """
    normalized = _normalize_repo_rel_path(path)
    if not normalized:
        return False
    parts = [part for part in normalized.split("/") if part]
    if any(part.lower() in _TEST_DIR_NAMES for part in parts[:-1]):
        return True
    filename = parts[-1] if parts else normalized
    return bool(_TEST_FILE_RE.match(filename))


def _numstat_summary(text: str, *, non_test_only: bool = False) -> tuple[dict, list[str]]:
    """Summarize ``git diff --numstat`` output.

    Returns ``({"files", "insertions", "deletions"}, paths)``. Binary file
    entries count as a changed file with zero textual insertions/deletions.
    """
    files = 0
    insertions = 0
    deletions = 0
    paths: list[str] = []
    for line in str(text or "").strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        path = parts[2]
        if non_test_only and _is_test_path(path):
            continue
        try:
            ins = int(parts[0]) if parts[0] != "-" else 0
            dels = int(parts[1]) if parts[1] != "-" else 0
        except ValueError:
            continue
        insertions += ins
        deletions += dels
        files += 1
        paths.append(path)
    return (
        {"files": files, "insertions": insertions, "deletions": deletions},
        paths,
    )


def _find_untracked_overwrite_paths(untracked_paths: list[str],
                                    target_paths: list[str]) -> list[str]:
    """Return untracked paths that collide with a checkout/update target set."""
    normalized_untracked = sorted({
        _normalize_repo_rel_path(path) for path in untracked_paths if path
    })
    normalized_targets = sorted({
        _normalize_repo_rel_path(path) for path in target_paths if path
    })
    if not normalized_untracked or not normalized_targets:
        return []

    matches: set[str] = set()
    for untracked in normalized_untracked:
        for target in normalized_targets:
            if (
                untracked == target
                or untracked.startswith(target + "/")
                or target.startswith(untracked + "/")
            ):
                matches.add(untracked)
                break
    return sorted(matches)


def _parse_worktree_list_porcelain(raw_text: str) -> list[dict]:
    """Parse ``git worktree list --porcelain`` output."""
    entries: list[dict] = []
    current: dict | None = None

    def _finish_current() -> None:
        nonlocal current
        if current is None:
            return
        entries.append(current)
        current = None

    for raw_line in raw_text.splitlines():
        line = raw_line.rstrip("\n")
        if not line:
            _finish_current()
            continue
        if line.startswith("worktree "):
            _finish_current()
            current = {
                "path": line[len("worktree "):].strip(),
                "head_sha": "",
                "branch_ref": "",
                "branch": "",
                "bare": False,
                "detached": False,
                "locked": False,
                "locked_reason": "",
                "prunable": False,
                "prunable_reason": "",
            }
            continue
        if current is None:
            continue
        if line.startswith("HEAD "):
            current["head_sha"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            branch_ref = line[len("branch "):].strip()
            current["branch_ref"] = branch_ref
            current["branch"] = branch_ref.removeprefix("refs/heads/")
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line.startswith("locked"):
            current["locked"] = True
            current["locked_reason"] = line[len("locked"):].strip()
        elif line.startswith("prunable"):
            current["prunable"] = True
            current["prunable_reason"] = line[len("prunable"):].strip()
    _finish_current()
    return entries


def _diff_status_from_name_status(code: str) -> str:
    """Normalize git ``--name-status`` codes to stable status labels."""
    return {
        "A": "added",
        "C": "copied",
        "D": "deleted",
        "M": "modified",
        "R": "renamed",
        "T": "type_changed",
        "U": "unmerged",
    }.get((code or "M")[:1], "modified")


def _short_sha(sha: str) -> str:
    return str(sha or "").strip()[:8]


_GITHUB_PR_VIEW_FIELDS = ",".join([
    "url",
    "number",
    "body",
    "headRefOid",
    "state",
    "mergeCommit",
    "mergedAt",
    "mergeStateStatus",
    "mergeable",
    "reviewDecision",
    "statusCheckRollup",
])
_PR_NUMBER_RE = re.compile(r"/pull/(\d+)(?:$|[/?#])")



class WorktreeRefreshError(RuntimeError):
    """Non-fatal refresh probe failure that must preserve prior state."""

    def __init__(self, kind: str, message: str, *, command: str = ""):
        super().__init__(message)
        self.kind = str(kind or "failure")
        self.command = str(command or "")


def _worktree_ok(phase: str, **extra) -> dict:
    result = {"ok": True, "phase": phase}
    result.update(extra)
    return result


def _worktree_error(phase: str, error: str, **extra) -> dict:
    result = {"ok": False, "phase": phase, "error": error}
    result.update(extra)
    return result


def _decode_process_output(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").strip()
    return str(raw).strip()


def _push_result_text(result: dict | None) -> str:
    result = result or {}
    return str(
        result.get("error")
        or result.get("stderr")
        or result.get("stdout")
        or ""
    ).strip()


def _is_non_fast_forward_push_error(text: str) -> bool:
    lower = str(text or "").lower()
    if not lower:
        return False
    return (
        "non-fast-forward" in lower
        or ("[rejected]" in lower and "fetch first" in lower)
        or ("updates were rejected" in lower and "behind" in lower)
    )


def _is_no_commits_between_pr_create_error(
        text: str,
        base_branch: str = "",
        branch: str = "",
) -> bool:
    """Return true for GitHub's already-landed/no-op PR error."""
    lower = str(text or "").lower()
    if "no commits between" not in lower:
        return False
    base = str(base_branch or "").strip().lower()
    head = str(branch or "").strip().lower()
    if base and base not in lower:
        return False
    # Branch names may be truncated/escaped by GitHub, so don't require the
    # head branch to be present.  If it is present, this is still the same
    # already-merged/no-op race we want to tolerate.
    return True


def _extract_pr_number_from_url(url: str) -> int | None:
    match = _PR_NUMBER_RE.search(str(url or "").strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _merge_commit_sha_from_pr_data(data: dict) -> str:
    merge_commit = data.get("mergeCommit") if isinstance(data, dict) else None
    if isinstance(merge_commit, dict):
        return str(
            merge_commit.get("oid")
            or merge_commit.get("sha")
            or merge_commit.get("id")
            or ""
        ).strip()
    if merge_commit:
        return str(merge_commit).strip()
    return ""


def _pr_result_from_view_data(data: dict, *, phase: str = "pr_view",
                              existing: bool | None = None) -> dict:
    data = data if isinstance(data, dict) else {}
    url = str(data.get("url") or "").strip()
    number = data.get("number")
    if number in {"", None}:
        number = _extract_pr_number_from_url(url)
    try:
        number = int(number) if number not in {"", None} else None
    except (TypeError, ValueError):
        number = None
    result = _worktree_ok(
        phase,
        url=url,
        number=number,
        body=str(data.get("body") or ""),
        head_sha=str(data.get("headRefOid") or "").strip(),
        merge_commit_sha=_merge_commit_sha_from_pr_data(data),
        state=str(data.get("state") or "").strip(),
        merged_at=str(data.get("mergedAt") or "").strip(),
        merge_state=str(data.get("mergeStateStatus") or "").strip(),
        mergeable=data.get("mergeable"),
        review_decision=data.get("reviewDecision"),
    )
    if "statusCheckRollup" in data:
        result["status_check_rollup"] = data.get("statusCheckRollup")
    if existing is not None:
        result["existing"] = existing
    return result


def _is_github_remote_url(url: str) -> bool:
    return "github.com" in str(url or "").lower()


def _github_host_from_url(url: str) -> str:
    """Extract the hostname from a git remote URL.

    Handles scp-like (``git@host:owner/repo.git``) and URL-like
    (``https://host/owner/repo.git``, ``ssh://git@host:22/...``) forms.
    Returns the lowercased host, or ``""`` when it cannot be determined.
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    # URL-like: scheme://[user@]host[:port]/...
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://(?:[^@/]+@)?([^:/]+)", raw)
    if m:
        return m.group(1).lower()
    # scp-like: [user@]host:owner/repo.git
    scp = re.match(r"^(?:[^@/]+@)?([^:/]+):", raw)
    if scp:
        return scp.group(1).lower()
    return ""


def _select_github_remote_from_remote_v(stdout: str) -> tuple[str, str]:
    """Pick the preferred GitHub remote from ``git remote -v`` output.

    Prefers ``origin`` when it is a GitHub remote, otherwise the first
    GitHub remote in declaration order. Returns ``(remote_name, url)`` or
    ``("", "")`` when no GitHub remote is present.
    """
    first_urls: dict[str, str] = {}
    order: list[str] = []
    for raw_line in (stdout or "").splitlines():
        parts = raw_line.split()
        if len(parts) < 2:
            continue
        name, url = parts[0], parts[1]
        if name not in first_urls:
            first_urls[name] = url
            order.append(name)

    github_remotes = [
        name for name in order if _is_github_remote_url(first_urls[name])
    ]
    if not github_remotes:
        return "", ""
    remote = "origin" if "origin" in github_remotes else github_remotes[0]
    return remote, first_urls.get(remote, "")


def stale_base_post_rebase_evidence_template(info: dict | None) -> dict:
    """Return the standard evidence shape required after stale-base rebase."""
    info = info or {}
    base_branch = str(info.get("base_branch", "") or "").strip()
    return {
        "post_rebase_head_sha": "<HEAD after rebase>",
        "base_branch": base_branch,
        "base_head_sha": str(info.get("base_head", "") or "").strip(),
        "rerun_tests": ["<exact command(s) rerun after rebase>"],
        "review_boundary_updated": "<true|false>",
    }


def format_stale_base_warning(info: dict | None, *,
                              rebase_command: str = "") -> str:
    """Return the loud operator warning for a branch forked behind base."""
    info = info or {}
    branch = str(info.get("branch", "") or "worktree branch").strip()
    base = str(info.get("base_branch", "") or "base").strip()
    fork = _short_sha(info.get("fork_point", ""))
    base_head = _short_sha(info.get("base_head", ""))
    branch_head = _short_sha(info.get("branch_head", ""))
    remote_ref = str(info.get("remote_base_ref", "") or "").strip()
    remote_head = _short_sha(info.get("remote_base_head", ""))
    fork_subject = str(
        info.get("fork_point_subject", "") or "unknown subject"
    ).strip()
    base_subject = str(
        info.get("base_head_subject", "") or "unknown subject"
    ).strip()
    commits = int(info.get("commits_on_base", 0) or 0)
    files = int(info.get("files_changed_on_base", 0) or 0)
    rebase_command = str(rebase_command or "").strip()
    if not rebase_command:
        agent_hint = str(info.get("agent_hint", "") or "").strip()
        rebase_command = (
            f"engineer_rebase {agent_hint}".strip()
            if agent_hint else "engineer_rebase <worker>"
        )
    remote_line = (
        f"  Current {remote_ref} head: {remote_head}.\n"
        if remote_ref and remote_head else ""
    )
    evidence = stale_base_post_rebase_evidence_template(info)
    evidence_line = (
        "  Post-rebase evidence required: "
        "post_rebase_head_sha, base_head_sha, exact rerun_tests, "
        "review_boundary_updated."
    )
    return (
        f"⚠ STALE BASE: {branch} forks from {fork} ({fork_subject}).\n"
        f"  Current branch head: {branch_head}.\n"
        f"  Current {base} head: {base_head} ({base_subject}).\n"
        f"{remote_line}"
        f"  {commits} commits + {files} files changed on {base} since fork.\n"
        "  `engineer_diff summary` against this base WILL mis-classify\n"
        "  other-branch changes as deletions.\n"
        f"  Recommended: `{rebase_command}` then re-run diff before merge.\n"
        f"{evidence_line} Template: {evidence}"
    )


def _parse_name_status_z(raw: bytes) -> list[dict]:
    """Parse ``git diff --name-status -z`` output."""
    records: list[dict] = []
    tokens = raw.decode("utf-8", errors="replace").split("\0")
    index = 0
    while index < len(tokens) - 1:
        status_code = tokens[index]
        index += 1
        if not status_code:
            continue
        status = _diff_status_from_name_status(status_code)
        if status in {"renamed", "copied"}:
            if index + 1 >= len(tokens):
                break
            old_path = tokens[index]
            new_path = tokens[index + 1]
            index += 2
            records.append({
                "status": status,
                "old_path": old_path,
                "path": new_path or old_path,
            })
            continue
        if index >= len(tokens):
            break
        path = tokens[index]
        index += 1
        records.append({
            "status": status,
            "old_path": path if status == "deleted" else "",
            "path": path,
        })
    return records


def _parse_numstat_z(raw: bytes) -> list[dict]:
    """Parse ``git diff --numstat -z`` output."""
    records: list[dict] = []
    tokens = raw.decode("utf-8", errors="replace").split("\0")
    index = 0
    while index < len(tokens) - 1:
        token = tokens[index]
        index += 1
        if not token:
            continue
        parts = token.split("\t")
        if len(parts) < 3:
            continue
        insertions_raw, deletions_raw, path_token = parts[:3]
        if path_token:
            old_path = ""
            path = path_token
        else:
            if index + 1 >= len(tokens):
                break
            old_path = tokens[index]
            path = tokens[index + 1]
            index += 2
        records.append({
            "old_path": old_path,
            "path": path or old_path,
            "insertions": 0 if insertions_raw == "-" else int(insertions_raw),
            "deletions": 0 if deletions_raw == "-" else int(deletions_raw),
            "binary": insertions_raw == "-" or deletions_raw == "-",
        })
    return records


def _diff_summary_key(record: dict):
    """Key diff records so ``--numstat`` and ``--name-status`` can merge."""
    old_path = record.get("old_path", "")
    path = record.get("path", "")
    if old_path and old_path != path:
        return old_path, path
    return path


def _diff_signals(path: str, *, old_path: str = "", status: str = "modified",
                  insertions: int = 0, deletions: int = 0,
                  binary: bool = False) -> list[str]:
    """Return lightweight file-interest signals for review planning."""
    signals: list[str] = []
    combined = " ".join(part for part in [old_path, path] if part).lower()
    basename = os.path.basename(path).lower()
    churn = insertions + deletions

    if status == "deleted":
        signals.append("destructive")
    elif status in {"renamed", "copied"}:
        signals.append("move")
    if binary:
        signals.append("binary")
    if basename in _LOCKFILE_NAMES:
        signals.append("dependency_lockfile")
    if _MIGRATION_RE.search(combined):
        signals.append("migration")
    if _AUTH_RE.search(combined):
        signals.append("auth")
    if _PROMPT_RE.search(combined):
        signals.append("prompt")
    if _CONFIG_RE.search(combined):
        signals.append("config")
    if _BUILD_TEST_RE.search(combined):
        signals.append("build_or_test")
    if churn >= _HIGH_CHURN_THRESHOLD:
        signals.append("high_churn")
    return signals


# --- Out-of-scope diff flag (TORQUE:604 A2) --------------------------------
# Observability-only: a backend-scoped task whose diff reaches into
# frontend-only paths (ee/frontend, static/js, webview.html) is the motivating
# drift case. Deliberately COARSE and low-false-positive — when the task's
# declared domain is ambiguous, nothing is flagged. This never blocks or
# changes dispatch/merge/completion behavior.
_FRONTEND_DOMAIN_RE = re.compile(
    r"(^|/)(ee/frontend|static/js|static/css)(/|$)|(^|/)webview\.html$",
    re.IGNORECASE,
)
_FRONTEND_SPECIALIZATIONS = frozenset({"ui-ux", "desktop-shell"})
_BACKEND_SPECIALIZATIONS = frozenset({
    "orchestration-core",
    "worktree-release",
    "quality-observability",
    "prompts-config",
    "runtime-pty",
})
_FRONTEND_HINT_RE = re.compile(
    r"\b(frontend|webview|panelsmith)\b|ee/frontend|static/js|static/css|"
    r"ui[-/ ]?ux",
    re.IGNORECASE,
)
_BACKEND_HINT_RE = re.compile(
    r"\b(backend|orchestration|daemon|mcp|server|state)\b|"
    r"server\.py|state\.py|(^|\s|/)torque/\w",
    re.IGNORECASE,
)
_CROSS_SURFACE_LABELS = frozenset({
    "cross-surface",
    "cross-surface-scope",
    "cross-domain",
    "full-stack",
    "state-shape",
    "state-shape-change",
    "compact-snapshot",
    "compact-snapshots",
})
_CROSS_SURFACE_HINT_RE = re.compile(
    r"\b(cross[- ]?surface|cross[- ]?domain|full[- ]?stack|"
    r"state[- ]?shape|compact[- ]?snapshot|frontend consumers?|"
    r"frontend-facing|consumer panels?|state consumers?)\b",
    re.IGNORECASE,
)
_FRONTEND_EXCLUSION_RE = re.compile(
    r"\b(no|not|avoid|exclude|excluding|without|skip|do not|don't)\b"
    r".{0,50}\b(frontend|webview|static/js|static/css|ee/frontend|ui[-/ ]?ux)\b|"
    r"\b(frontend|webview|static/js|static/css|ee/frontend|ui[-/ ]?ux)\b"
    r".{0,50}\b(out of scope|non[- ]?goal|excluded|do not touch)\b",
    re.IGNORECASE | re.DOTALL,
)


def is_worker_namespaced_branch(branch: str) -> bool:
    """Return whether *branch* is a worker-namespaced torque branch.

    Matches ``torque/<engineer-slug>/*`` and ``torque/user/*`` (a two-level
    torque branch). Flat branches (``main``, ``torque/<engineer-slug>-<id>``,
    detached ``HEAD``) are not worker-namespaced.
    """
    branch = str(branch or "").strip()
    return bool(branch) and bool(_WORKER_NAMESPACED_BRANCH_RE.match(branch))


def path_is_frontend_domain(path: str) -> bool:
    """Return whether *path* is a frontend-only file (coarse classifier)."""
    normalized = _normalize_repo_rel_path(path)
    return bool(normalized) and bool(_FRONTEND_DOMAIN_RE.search(normalized))


def _normalized_labels(labels) -> list[str]:
    return [
        str(label or "").strip().lower()
        for label in (labels or [])
        if str(label or "").strip()
    ]


def _scope_text(*, labels=None, description: str = "", task: str = "",
                context: str = "", criteria: str = "") -> str:
    return " ".join([
        *_normalized_labels(labels),
        str(task or ""),
        str(description or ""),
        str(context or ""),
        str(criteria or ""),
    ])


def _domain_from_specialization(specialization: str) -> tuple[str | None, str]:
    spec = str(specialization or "").strip().lower()
    if spec in _FRONTEND_SPECIALIZATIONS:
        return "frontend", f"suggested specialization '{spec}' maps to frontend"
    if spec in _BACKEND_SPECIALIZATIONS:
        return "backend", f"suggested specialization '{spec}' maps to backend"
    return None, ""


def _domain_from_text(text: str) -> tuple[str | None, str]:
    has_frontend = bool(_FRONTEND_HINT_RE.search(text))
    has_backend = bool(_BACKEND_HINT_RE.search(text))
    if has_backend and not has_frontend:
        return "backend", "task text mentions backend/server scope only"
    if has_frontend and not has_backend:
        return "frontend", "task text mentions frontend/webview scope only"
    return None, ""


def _cross_surface_allowance(labels, text: str) -> tuple[bool, str]:
    """Return whether task text explicitly allows frontend reach for backend.

    This is intentionally conservative: generic mixed backend+frontend mentions
    are not enough when a backend specialization is present. The task needs a
    cross-surface label or phrasing that explains why frontend consumers are in
    scope (state-shape/compact-snapshot/full-stack/etc.). Negative scope such as
    "NO frontend" always wins.
    """
    if _FRONTEND_EXCLUSION_RE.search(text):
        return False, ""
    label_hits = [
        label for label in _normalized_labels(labels)
        if label in _CROSS_SURFACE_LABELS
    ]
    if label_hits:
        return True, (
            "task label explicitly allows cross-surface work: "
            + ", ".join(label_hits)
        )
    has_frontend = bool(_FRONTEND_HINT_RE.search(text))
    has_backend = bool(_BACKEND_HINT_RE.search(text))
    if has_frontend and has_backend and _CROSS_SURFACE_HINT_RE.search(text):
        return True, (
            "task scope describes a cross-surface/state-shape change with "
            "frontend consumers"
        )
    return False, ""


def build_diff_scope_context(*, specialization: str = "", labels=None,
                             description: str = "", task: str = "",
                             context: str = "",
                             criteria: str = "") -> dict:
    """Build explainable task-scope metadata for diff classification.

    The returned dict is advisory/observability-only.  ``domain`` is the
    primary declared domain used for foreign-path warnings.  Known legitimate
    cross-surface patterns (for example state-shape or compact-snapshot work
    with frontend consumers) are captured in ``allowed_foreign_domains`` so a
    backend-routed task can touch frontend consumers without a false alarm.
    """
    text = _scope_text(
        labels=labels,
        description=description,
        task=task,
        context=context,
        criteria=criteria,
    )
    domain, reason = _domain_from_specialization(specialization)
    if not domain:
        domain, reason = _domain_from_text(text)
    scope = {
        "domain": domain,
        "domain_reason": reason,
        "allowed_foreign_domains": [],
        "allowed_foreign_reasons": {},
    }
    allow_frontend, allow_reason = _cross_surface_allowance(labels, text)
    if domain == "backend" and allow_frontend:
        scope["allowed_foreign_domains"].append("frontend")
        scope["allowed_foreign_reasons"]["frontend"] = allow_reason
    return scope


def _normalize_diff_scope_context(scope) -> dict:
    if isinstance(scope, dict):
        return {
            "domain": scope.get("domain"),
            "domain_reason": scope.get("domain_reason", ""),
            "allowed_foreign_domains": list(
                scope.get("allowed_foreign_domains") or []
            ),
            "allowed_foreign_reasons": dict(
                scope.get("allowed_foreign_reasons") or {}
            ),
        }
    return {
        "domain": scope,
        "domain_reason": "",
        "allowed_foreign_domains": [],
        "allowed_foreign_reasons": {},
    }


def classify_task_scope_domain(*, specialization: str = "",
                               labels=None,
                               description: str = "") -> Optional[str]:
    """Coarsely classify a task's declared domain for out-of-scope flagging.

    Returns ``"backend"`` or ``"frontend"`` only when the task carries a clear
    domain signal; ``None`` (don't flag) when ambiguous. The structured
    specialization slug wins unless task labels/description explicitly describe
    a known cross-surface pattern where frontend consumers are in scope.
    """
    scope = build_diff_scope_context(
        specialization=specialization,
        labels=labels,
        description=description,
    )
    if scope["domain"] == "backend" \
            and "frontend" in scope["allowed_foreign_domains"]:
        return None
    return scope["domain"]


def out_of_scope_diff_paths(scope, paths) -> list[str]:
    """Return diff paths that fall in a foreign domain for *scope*.

    Coarse + low-false-positive: only the motivating backend->frontend case is
    flagged today. An unknown/None domain never flags.
    """
    scope_context = _normalize_diff_scope_context(scope)
    if scope_context.get("domain") != "backend":
        return []
    if "frontend" in set(scope_context.get("allowed_foreign_domains") or []):
        return []
    return sorted({
        _normalize_repo_rel_path(p)
        for p in (paths or [])
        if p and path_is_frontend_domain(p)
    })


def out_of_scope_diff_classification(scope, paths) -> dict:
    """Return explainable out-of-scope path details for a diff."""
    normalized = _normalize_diff_scope_context(scope)
    out_paths = out_of_scope_diff_paths(normalized, paths)
    domain = normalized.get("domain")
    path_reasons = {}
    if domain == "backend":
        for path in out_paths:
            path_reasons[path] = (
                "frontend-only path (static/js, static/css, ee/frontend, or "
                "webview.html) in a backend-scoped task"
            )
    return {
        "domain": domain,
        "domain_reason": normalized.get("domain_reason", ""),
        "paths": out_paths,
        "count": len(out_paths),
        "path_reasons": path_reasons,
        "allowed_foreign_domains": normalized.get("allowed_foreign_domains", []),
        "allowed_foreign_reasons": normalized.get("allowed_foreign_reasons", {}),
    }


def ensure_git_exclude(directory: str) -> None:
    """Add Torque-injected filenames to .git/info/exclude if not present.

    Works for both normal repos (.git is a directory) and worktrees
    (.git is a file pointing to the real gitdir).
    """
    directory = os.path.expanduser(directory)
    dot_git = os.path.join(directory, ".git")
    try:
        if os.path.isfile(dot_git):
            # Worktree — .git is a file with "gitdir: <path>"
            with open(dot_git) as f:
                gitdir = f.read().strip().removeprefix("gitdir: ")
                if not os.path.isabs(gitdir):
                    gitdir = os.path.normpath(
                        os.path.join(directory, gitdir))
        elif os.path.isdir(dot_git):
            gitdir = dot_git
        else:
            return

        exclude = os.path.join(gitdir, "info", "exclude")
        os.makedirs(os.path.dirname(exclude), exist_ok=True)

        existing = set()
        if os.path.exists(exclude):
            with open(exclude) as f:
                existing = set(f.read().splitlines())

        to_add = [e for e in TORQUE_EXCLUDE_ENTRIES if e not in existing]
        if to_add:
            with open(exclude, "a") as f:
                for entry in to_add:
                    f.write(f"{entry}\n")
            log.debug("Added %d entries to git exclude: %s",
                      len(to_add), exclude)
    except Exception:
        log.debug("Could not update git exclude in %s", directory)


# Worktree-isolation guard: a managed ``pre-commit`` hook installed in the
# shared repo. It refuses commits made into the SHARED main checkout from
# inside a Torque worker session (``TORQUE_CELL_ID`` set), while leaving
# human commits and isolated-worktree commits untouched. Linked worktrees
# share the common hooks dir, so this single hook covers every worktree and
# the main checkout; it discriminates via git-dir vs git-common-dir.
#
# This is the durable fail-closed fix for the worktree-isolation breach where
# a worker's ``git commit`` landed on the main checkout HEAD (TORQUE:580).
_ISOLATION_GUARD_HOOK_MARKER = "torque-worktree-isolation-guard"
_ISOLATION_GUARD_HOOK_VERSION = 1
_ISOLATION_GUARD_HOOK_SCRIPT = f"""#!/bin/sh
# {_ISOLATION_GUARD_HOOK_MARKER} v{_ISOLATION_GUARD_HOOK_VERSION} (managed by Torque)
# Refuses commits made into the SHARED main checkout from inside a Torque
# worker session. Worker git changes must stay in the isolated worktree
# (.torque/worktrees/<id>). See TORQUE:580. Edit via Torque, not by hand.
#
# Human commits (no TORQUE_CELL_ID) and commits inside a linked worktree are
# always allowed; only a worker committing into the main checkout is blocked.
[ -n "${{TORQUE_CELL_ID}}" ] || exit 0

_abspath() {{ ( cd "$1" 2>/dev/null && pwd ) ; }}
_git_dir=$(git rev-parse --git-dir 2>/dev/null) || exit 0
_common_dir=$(git rev-parse --git-common-dir 2>/dev/null) || exit 0
_git_dir=$(_abspath "${{_git_dir}}")
_common_dir=$(_abspath "${{_common_dir}}")

# A linked worktree has a distinct git-dir from the common dir -> allow.
if [ -z "${{_git_dir}}" ] || [ "${{_git_dir}}" != "${{_common_dir}}" ]; then
    exit 0
fi

echo "torque: BLOCKED commit into the shared main checkout from worker session ${{TORQUE_CELL_ID}}." >&2
echo "torque: worker git changes must stay in your isolated worktree (.torque/worktrees/<id>)." >&2
echo "torque: cd into your assigned worktree and commit there." >&2
echo "torque: (worktree-isolation guard, TORQUE:580)" >&2
exit 1
"""


def _resolve_hooks_dir(repo_root: str) -> Optional[str]:
    """Return the git hooks directory for *repo_root* (main checkout).

    Honours ``core.hooksPath`` when configured, otherwise uses
    ``<git-common-dir>/hooks``. Returns ``None`` when git can't be queried.
    """
    repo_root = os.path.expanduser(repo_root)
    try:
        hooks_path = subprocess.run(
            ["git", "-C", repo_root, "config", "--get", "core.hooksPath"],
            capture_output=True, text=True, timeout=10,
        )
        if hooks_path.returncode == 0:
            configured = hooks_path.stdout.strip()
            if configured:
                if not os.path.isabs(configured):
                    configured = os.path.join(repo_root, configured)
                return configured

        common = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=10,
        )
        if common.returncode != 0:
            return None
        git_common = common.stdout.strip()
        if not git_common:
            return None
        if not os.path.isabs(git_common):
            git_common = os.path.join(repo_root, git_common)
        return os.path.join(os.path.normpath(git_common), "hooks")
    except Exception:
        log.debug("Could not resolve hooks dir for %s", repo_root, exc_info=True)
        return None


def ensure_worktree_isolation_guard(repo_root: str) -> str:
    """Install/refresh the worktree-isolation ``pre-commit`` guard hook.

    Idempotent and conservative:
      - installs the managed hook when no ``pre-commit`` hook exists;
      - refreshes it when an older managed version is present;
      - never clobbers a foreign (user-authored) ``pre-commit`` hook — it
        logs a warning and leaves the foreign hook in place.

    Returns one of: ``"installed"``, ``"refreshed"``, ``"present"``,
    ``"foreign"``, ``"skipped"`` (no repo / git unavailable).
    """
    if not repo_root:
        return "skipped"
    hooks_dir = _resolve_hooks_dir(repo_root)
    if not hooks_dir:
        return "skipped"
    pre_commit = os.path.join(hooks_dir, "pre-commit")
    try:
        existing = ""
        if os.path.exists(pre_commit):
            with open(pre_commit, encoding="utf-8", errors="replace") as f:
                existing = f.read()
            if _ISOLATION_GUARD_HOOK_MARKER not in existing:
                log.warning(
                    "Worktree-isolation guard: a non-Torque pre-commit hook "
                    "already exists at %s; leaving it untouched. Worker "
                    "commits into the shared main checkout will NOT be "
                    "blocked by the hook until it is merged with Torque's "
                    "guard (the daemon-side guard still applies).",
                    pre_commit,
                )
                return "foreign"
            if existing == _ISOLATION_GUARD_HOOK_SCRIPT:
                # Already current; just make sure it stays executable.
                _make_executable(pre_commit)
                return "present"

        os.makedirs(hooks_dir, exist_ok=True)
        with open(pre_commit, "w", encoding="utf-8") as f:
            f.write(_ISOLATION_GUARD_HOOK_SCRIPT)
        _make_executable(pre_commit)
        outcome = "refreshed" if existing else "installed"
        log.info("Worktree-isolation guard %s at %s", outcome, pre_commit)
        return outcome
    except Exception:
        log.debug("Could not install worktree-isolation guard at %s",
                  pre_commit, exc_info=True)
        return "skipped"


def _make_executable(path: str) -> None:
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | 0o111)
    except OSError:
        log.debug("Could not chmod +x %s", path, exc_info=True)


def worktree_isolation_guard_installed(repo_root: str) -> bool:
    """Return True when Torque's managed pre-commit guard is present."""
    if not repo_root:
        return False
    hooks_dir = _resolve_hooks_dir(repo_root)
    if not hooks_dir:
        return False
    pre_commit = os.path.join(hooks_dir, "pre-commit")
    try:
        if not os.path.exists(pre_commit):
            return False
        with open(pre_commit, encoding="utf-8", errors="replace") as f:
            return _ISOLATION_GUARD_HOOK_MARKER in f.read()
    except OSError:
        return False


def worktree_dir_is_shared_checkout(cell) -> bool:
    """True when the cell's worktree_path resolves to its shared repo root.

    A worker/engineer/architect worktree must live under
    ``.torque/worktrees/<id>`` (or a flat sibling), never at the repo root
    itself. If they coincide, any mutating git op would contaminate the
    shared main checkout — the exact failure TORQUE:580 guards against.
    """
    wt = (getattr(cell, "worktree_path", "") or "").strip()
    root = (getattr(cell, "worktree_repo_root", "") or "").strip()
    if not wt or not root:
        return False
    try:
        return os.path.realpath(os.path.expanduser(wt)) == \
            os.path.realpath(os.path.expanduser(root))
    except OSError:
        return False


def _write_claude_code_local_settings(worktree_path: str) -> None:
    """Merge Torque's Claude Code local settings into a worktree."""
    settings_dir = os.path.join(worktree_path, _CLAUDE_CODE_SETTINGS_DIR)
    settings_path = os.path.join(settings_dir, _CLAUDE_CODE_SETTINGS_FILE)
    os.makedirs(settings_dir, exist_ok=True)

    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                settings = loaded
            else:
                log.warning(
                    "Replacing non-object Claude Code settings in %s",
                    settings_path,
                )
        except json.JSONDecodeError:
            log.warning(
                "Replacing invalid Claude Code settings JSON in %s",
                settings_path,
            )

    settings["autoMemoryEnabled"] = False
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")


def _configure_claude_code_worktree_settings(cell, worktree_path: str) -> None:
    """Disable Claude Code auto-memory for agent kinds that need isolation."""
    kind = str(getattr(cell, "kind", "") or "").strip().lower()
    if kind not in _CLAUDE_CODE_AUTO_MEMORY_DISABLED_KINDS:
        return
    try:
        _write_claude_code_local_settings(worktree_path)
    except Exception:
        log.exception(
            "Failed to write Claude Code local settings for '%s'",
            getattr(cell, "name", ""),
        )


def _slugify_worktree_name(name: str, max_len: int = _WORKTREE_NAME_MAX_LEN) -> str:
    """Normalize a user-supplied worktree name to one safe slug segment."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if max_len > 0 and len(slug) > max_len:
        slug = slug[:max_len].strip("-")
    return slug


def _dedupe_worktree_name(base: str, index: int,
                          max_len: int = _WORKTREE_NAME_MAX_LEN) -> str:
    """Append a numeric suffix while keeping the worktree name bounded."""
    if index <= 1:
        return base
    suffix = f"-{index}"
    trimmed = base
    if max_len > 0 and len(trimmed) + len(suffix) > max_len:
        trimmed = trimmed[:max_len - len(suffix)].rstrip("-")
    trimmed = trimmed or "worktree"
    return f"{trimmed}{suffix}"


def _agent_branch_slug(agent) -> str:
    """Return the agent slug segment used in default worktree branch names."""
    slug = str(getattr(agent, "slug", "") or "").strip()
    if slug:
        return slug
    return _slugify_worktree_name(getattr(agent, "name", ""), max_len=30) or "unnamed"


def _branch_prefix_for_agent(agent, state) -> str:
    """Return the creation-time worktree branch prefix for an agent.

    Worker branches intentionally capture who owned the worker when the
    worktree was created. If an engineer is later deleted and the worker
    transfers back to the user, the existing branch name is grandfathered.
    """
    if str(getattr(agent, "kind", "") or "").strip() != "worker":
        return "torque/"

    owner_engineer_id = str(
        getattr(agent, "owner_engineer_id", "") or ""
    ).strip()
    if not owner_engineer_id:
        return "torque/user/"

    owner = None
    if state is not None:
        owner = getattr(state, "agents", {}).get(owner_engineer_id)
    owner_slug = _agent_branch_slug(owner) if owner else ""
    if owner_slug:
        return f"torque/{owner_slug}/"
    return "torque/user/"


def _nested_submodule_branch_name(submodule_path: str,
                                  super_branch: str) -> str:
    """Derive a branch name for a linked submodule worktree.

    The branch lives inside the submodule repository, so include the submodule
    path namespace plus the superproject worker branch. This prevents two
    configured submodules from colliding even when their super worktree branch
    names match.
    """
    sub_slug = _slugify_worktree_name(
        _normalize_repo_rel_path(submodule_path).replace("/", "-"),
        max_len=60,
    ) or "submodule"
    branch = str(super_branch or "").strip()
    if not branch or branch == "HEAD":
        branch = "detached"
    # Existing Torque branch names are already git-ref-safe, but keep the
    # derived submodule name fail-closed for any future/custom branch source.
    branch = re.sub(r"[^A-Za-z0-9._/-]+", "-", branch)
    branch = re.sub(r"/+", "/", branch).strip("/.")
    branch = branch or "worktree"
    return f"torque/submodules/{sub_slug}/{branch}"


def _custom_branch_leaf_for_agent(agent, candidate: str) -> str:
    """Return the custom branch leaf for a worktree target.

    Worker custom worktree names still need to follow the Stage 5 branch
    contract (`<slug>-<shortid>` under the engineer/user namespace), so append
    the agent short id to the sanitized custom name. Engineer/architect custom
    branches keep the existing flat custom leaf because they are already the
    ownership root.
    """
    candidate = str(candidate or "").strip()
    if not candidate:
        return candidate
    if str(getattr(agent, "kind", "") or "").strip() != "worker":
        return candidate
    short_id = str(getattr(agent, "id", "") or "").strip()[:7]
    if not short_id:
        return candidate
    return f"{candidate}-{short_id}"


def _resolve_worktree_base_path(repo_root: str, base_dir: str) -> str:
    """Return the absolute base directory under which worktrees are created."""
    repo_root = os.path.realpath(os.path.expanduser(repo_root))
    base_dir = os.path.expanduser(base_dir or ".torque/worktrees")
    if os.path.isabs(base_dir):
        return os.path.realpath(base_dir)
    return os.path.realpath(os.path.join(repo_root, base_dir))


def _repo_root_from_common_dir(common_dir: str) -> str:
    common_dir = os.path.realpath(os.path.expanduser(common_dir or ""))
    if not common_dir:
        return ""
    if os.path.basename(common_dir) == ".git":
        return os.path.dirname(common_dir)
    return ""


# Export underscored compatibility helpers as well as public support symbols.
__all__ = [name for name in globals() if not name.startswith("__")]
