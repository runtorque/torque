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

from .config import log

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


def format_stale_base_warning(info: dict | None, *,
                              rebase_command: str = "") -> str:
    """Return the loud operator warning for a branch forked behind base."""
    info = info or {}
    branch = str(info.get("branch", "") or "worktree branch").strip()
    base = str(info.get("base_branch", "") or "base").strip()
    fork = _short_sha(info.get("fork_point", ""))
    base_head = _short_sha(info.get("base_head", ""))
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
    return (
        f"⚠ STALE BASE: {branch} forks from {fork} ({fork_subject}).\n"
        f"  {base} has advanced to {base_head} ({base_subject}).\n"
        f"  {commits} commits + {files} files changed on {base} since fork.\n"
        "  `engineer_diff summary` against this base WILL mis-classify\n"
        "  other-branch changes as deletions.\n"
        f"  Recommended: `{rebase_command}` then re-run diff before merge."
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
    r"\b(frontend|webview|panelsmith)\b|ee/frontend|static/js|ui[-/ ]?ux",
    re.IGNORECASE,
)
_BACKEND_HINT_RE = re.compile(
    r"\b(backend|orchestration|daemon|mcp)\b|server\.py|state\.py|"
    r"(^|\s|/)torque/\w",
    re.IGNORECASE,
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


def classify_task_scope_domain(*, specialization: str = "",
                               labels=None,
                               description: str = "") -> Optional[str]:
    """Coarsely classify a task's declared domain for out-of-scope flagging.

    Returns ``"backend"`` or ``"frontend"`` only when the task carries a clear
    domain signal; ``None`` (don't flag) when ambiguous. The structured
    specialization slug wins; otherwise a low-false-positive text heuristic is
    used, and a task that mentions BOTH domains stays ``None``.
    """
    spec = str(specialization or "").strip().lower()
    if spec in _FRONTEND_SPECIALIZATIONS:
        return "frontend"
    if spec in _BACKEND_SPECIALIZATIONS:
        return "backend"
    text = " ".join([*(labels or []), str(description or "")])
    has_frontend = bool(_FRONTEND_HINT_RE.search(text))
    has_backend = bool(_BACKEND_HINT_RE.search(text))
    if has_backend and not has_frontend:
        return "backend"
    if has_frontend and not has_backend:
        return "frontend"
    return None


def out_of_scope_diff_paths(domain: Optional[str], paths) -> list[str]:
    """Return diff paths that fall in a foreign domain for *domain*.

    Coarse + low-false-positive: only the motivating backend->frontend case is
    flagged today. An unknown/None domain never flags.
    """
    if domain != "backend":
        return []
    return sorted({
        _normalize_repo_rel_path(p)
        for p in (paths or [])
        if p and path_is_frontend_domain(p)
    })


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


class WorktreeManager:
    """Manages git worktrees for agent isolation."""

    def __init__(self, *,
                 refresh_git_timeout_seconds: float =
                 WORKTREE_REFRESH_GIT_TIMEOUT_SECONDS,
                 refresh_max_concurrent: int =
                 WORKTREE_REFRESH_MAX_CONCURRENT):
        # Per-cell ephemeral fingerprint of (worktree_index_mtime,
        # base_ref_mtime). Used by `refresh_state` to skip the entire
        # status/diff/ahead-behind/is_merged probe when neither side has
        # advanced since the last tick.
        self._refresh_fingerprints: dict[str, tuple[float, float]] = {}
        self.refresh_git_timeout_seconds = max(
            0.1,
            float(refresh_git_timeout_seconds or
                  WORKTREE_REFRESH_GIT_TIMEOUT_SECONDS),
        )
        self._refresh_semaphore = asyncio.Semaphore(
            max(1, int(refresh_max_concurrent or WORKTREE_REFRESH_MAX_CONCURRENT))
        )
        self._refresh_inflight: dict[str, asyncio.Task] = {}
        self._refresh_metrics: dict[str, float | int | str] = {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "timeouts": 0,
            "missing_worktrees": 0,
            "coalesced": 0,
            "skipped_unchanged": 0,
            "examined": 0,
            "last_duration_ms": 0.0,
            "max_duration_ms": 0.0,
            "last_error_kind": "",
            "last_error_cell": "",
            "last_error_command": "",
            "active": 0,
            "max_concurrent": max(
                1,
                int(refresh_max_concurrent or WORKTREE_REFRESH_MAX_CONCURRENT),
            ),
        }
        self._refresh_issue_log_at: dict[tuple[str, str], float] = {}

    def refresh_metrics_snapshot(self) -> dict:
        """Return low-noise counters for recent background refresh health."""
        return dict(self._refresh_metrics)

    def _record_refresh_metric(self, *,
                               outcome: str,
                               duration_ms: float = 0.0,
                               cell=None,
                               error_kind: str = "",
                               command: str = "") -> None:
        metrics = self._refresh_metrics
        if outcome == "attempt":
            metrics["attempts"] = int(metrics.get("attempts", 0) or 0) + 1
        elif outcome == "success":
            metrics["successes"] = int(metrics.get("successes", 0) or 0) + 1
            metrics["examined"] = int(metrics.get("examined", 0) or 0) + 1
        elif outcome == "failure":
            metrics["failures"] = int(metrics.get("failures", 0) or 0) + 1
            if error_kind == "timeout":
                metrics["timeouts"] = int(metrics.get("timeouts", 0) or 0) + 1
            elif error_kind == "missing_worktree":
                metrics["missing_worktrees"] = (
                    int(metrics.get("missing_worktrees", 0) or 0) + 1
                )
            metrics["last_error_kind"] = str(error_kind or "failure")
            metrics["last_error_cell"] = str(
                getattr(cell, "name", "") or getattr(cell, "id", "") or ""
            )
            metrics["last_error_command"] = str(command or "")
        elif outcome == "coalesced":
            metrics["coalesced"] = int(metrics.get("coalesced", 0) or 0) + 1
        elif outcome == "skipped_unchanged":
            metrics["skipped_unchanged"] = (
                int(metrics.get("skipped_unchanged", 0) or 0) + 1
            )
        if duration_ms >= 0:
            metrics["last_duration_ms"] = float(duration_ms)
            metrics["max_duration_ms"] = max(
                float(metrics.get("max_duration_ms", 0.0) or 0.0),
                float(duration_ms),
            )

    def _log_refresh_issue(self, cell, kind: str, message: str,
                           *, command: str = "") -> None:
        """Throttle repeated refresh diagnostics from the same cell/kind."""
        cell_key = str(getattr(cell, "id", "") or getattr(cell, "name", "") or "")
        key = (cell_key, str(kind or "failure"))
        now = time.monotonic()
        last = self._refresh_issue_log_at.get(key, 0.0)
        if now - last < WORKTREE_REFRESH_LOG_THROTTLE_SECONDS:
            return
        self._refresh_issue_log_at[key] = now
        detail = f" command={command}" if command else ""
        log.warning(
            "Worktree refresh %s for '%s': %s%s; preserving previous state",
            kind,
            getattr(cell, "name", "") or getattr(cell, "id", ""),
            message,
            detail,
        )

    async def _communicate_refresh_git(self, proc, *, command: str):
        timeout = self.refresh_git_timeout_seconds
        try:
            return await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    proc.wait(),
                    timeout=WORKTREE_REFRESH_KILL_GRACE_SECONDS,
                )
            raise WorktreeRefreshError(
                "timeout",
                f"git refresh command exceeded {timeout:.1f}s",
                command=command,
            ) from exc

    async def _refresh_git(self, directory: str, *args: str,
                           stderr_pipe: bool = False,
                           check: bool = True) -> tuple[int, str, str]:
        """Run a bounded git command used by background worktree refresh.

        Failures raise ``WorktreeRefreshError`` so refresh_state can leave
        previously visible worktree metadata untouched instead of replacing it
        with misleading zero/clean values.
        """
        command = "git -C {} {}".format(directory, " ".join(args))
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", directory, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=(
                    asyncio.subprocess.PIPE
                    if stderr_pipe else asyncio.subprocess.DEVNULL
                ),
            )
            stdout, stderr = await self._communicate_refresh_git(
                proc,
                command=command,
            )
        except WorktreeRefreshError:
            raise
        except Exception as exc:
            raise WorktreeRefreshError(
                "spawn_failed",
                str(exc) or "failed to start git refresh command",
                command=command,
            ) from exc
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = (
            stderr.decode("utf-8", errors="replace").strip()
            if stderr else ""
        )
        if check and proc.returncode != 0:
            raise WorktreeRefreshError(
                "git_failed",
                stderr_text or f"git exited with status {proc.returncode}",
                command=command,
            )
        return proc.returncode, stdout_text, stderr_text

    @staticmethod
    def _resolve_gitdir(worktree_path: str) -> str:
        """Resolve the actual git directory for a worktree path.

        For linked worktrees, ``<worktree_path>/.git`` is a file pointing
        to the gitdir under the main repo; for non-linked dirs it is the
        gitdir itself. Returns "" on failure.
        """
        if not worktree_path:
            return ""
        dot_git = os.path.join(worktree_path, ".git")
        if os.path.isdir(dot_git):
            return dot_git
        if os.path.isfile(dot_git):
            try:
                with open(dot_git) as f:
                    line = f.read().strip()
                if line.startswith("gitdir:"):
                    return line[len("gitdir:"):].strip()
            except OSError:
                return ""
        return ""

    @staticmethod
    def _ref_mtime(repo_root: str, branch: str) -> float:
        """Return the mtime of a branch ref (loose or packed). 0.0 on miss."""
        if not repo_root or not branch:
            return 0.0
        loose = os.path.join(repo_root, ".git", "refs", "heads", branch)
        try:
            return os.path.getmtime(loose)
        except OSError:
            pass
        packed = os.path.join(repo_root, ".git", "packed-refs")
        try:
            return os.path.getmtime(packed)
        except OSError:
            return 0.0

    @staticmethod
    def _common_gitdir_from_gitdir(gitdir: str) -> str:
        """Resolve the common git dir for a normal or linked worktree gitdir."""
        if not gitdir:
            return ""
        common_file = os.path.join(gitdir, "commondir")
        try:
            with open(common_file) as f:
                common = f.read().strip()
        except OSError:
            return gitdir
        if not common:
            return gitdir
        if not os.path.isabs(common):
            common = os.path.join(gitdir, common)
        return os.path.realpath(common)

    @staticmethod
    def _ref_mtime_in_gitdir(gitdir: str, branch: str) -> float:
        """Return branch ref mtime in a git dir/common dir. 0.0 on miss."""
        if not gitdir or not branch:
            return 0.0
        loose = os.path.join(gitdir, "refs", "heads", branch)
        try:
            return os.path.getmtime(loose)
        except OSError:
            pass
        packed = os.path.join(gitdir, "packed-refs")
        try:
            return os.path.getmtime(packed)
        except OSError:
            return 0.0

    def _refresh_fingerprint(self, cell, worktree_submodules=None) -> tuple:
        """Cheap fingerprint that changes only when the worktree or base moved."""
        gitdir = self._resolve_gitdir(cell.worktree_path or "")
        index_mtime = 0.0
        if gitdir:
            try:
                index_mtime = os.path.getmtime(os.path.join(gitdir, "index"))
            except OSError:
                index_mtime = 0.0
        base_mtime = self._ref_mtime(
            cell.worktree_repo_root or "",
            cell.worktree_base_branch or "",
        )
        submodule_paths = _normalize_worktree_submodules(worktree_submodules)
        if not submodule_paths:
            return (index_mtime, base_mtime)

        nested_mtimes: list[float] = []
        base_branch = str(getattr(cell, "worktree_base_branch", "") or "")
        for sub_path in submodule_paths:
            sub_wt = self._join_repo_rel(cell.worktree_path or "", sub_path)
            sub_gitdir = self._resolve_gitdir(sub_wt)
            for rel in ("index", "HEAD"):
                if not sub_gitdir:
                    nested_mtimes.append(0.0)
                    continue
                try:
                    nested_mtimes.append(os.path.getmtime(
                        os.path.join(sub_gitdir, rel)))
                except OSError:
                    nested_mtimes.append(0.0)
            common_gitdir = self._common_gitdir_from_gitdir(sub_gitdir)
            nested_mtimes.append(
                self._ref_mtime_in_gitdir(common_gitdir, base_branch)
            )
        return (index_mtime, base_mtime, *nested_mtimes)

    async def refresh_state(self, cell, worktree_submodules=None) -> bool:
        """Refresh worktree-derived ephemeral fields on ``cell`` in one pass.

        Returns True if any field changed. Skips the work entirely when
        the cheap mtime fingerprint matches the last successful refresh,
        which is the common case (most agents are idle most ticks). Git
        subprocesses in this refresh path are bounded and failures preserve
        previously visible state rather than overwriting it with false
        clean/ready values.
        """
        if not cell.worktree_path or not cell.worktree_base_branch:
            return False
        if not os.path.isdir(cell.worktree_path):
            self._record_refresh_metric(
                outcome="failure",
                cell=cell,
                error_kind="missing_worktree",
            )
            self._log_refresh_issue(
                cell,
                "missing_worktree",
                f"path does not exist: {cell.worktree_path}",
            )
            return False

        fingerprint = self._refresh_fingerprint(cell, worktree_submodules)
        previous = self._refresh_fingerprints.get(cell.id)
        if previous == fingerprint and previous != (0.0, 0.0):
            self._record_refresh_metric(outcome="skipped_unchanged")
            return False

        inflight = self._refresh_inflight.get(cell.id)
        if inflight is not None and not inflight.done():
            self._record_refresh_metric(outcome="coalesced")
            return await asyncio.shield(inflight)

        task = asyncio.create_task(
            self._refresh_state_with_limit(
                cell,
                fingerprint,
                worktree_submodules=worktree_submodules,
            )
        )
        self._refresh_inflight[cell.id] = task
        try:
            return await task
        finally:
            if self._refresh_inflight.get(cell.id) is task:
                self._refresh_inflight.pop(cell.id, None)

    async def _refresh_state_with_limit(self, cell, fingerprint: tuple,
                                        worktree_submodules=None) -> bool:
        """Run one refresh under the manager-wide concurrency cap."""
        async with self._refresh_semaphore:
            self._refresh_metrics["active"] = (
                int(self._refresh_metrics.get("active", 0) or 0) + 1
            )
            try:
                return await self._refresh_state_inner(
                    cell,
                    fingerprint,
                    worktree_submodules=worktree_submodules,
                )
            finally:
                self._refresh_metrics["active"] = max(
                    0,
                    int(self._refresh_metrics.get("active", 0) or 0) - 1,
                )

    async def _refresh_state_inner(self, cell, fingerprint: tuple,
                                   worktree_submodules=None) -> bool:
        """Uncoalesced refresh body. Caller owns concurrency limiting."""
        started = time.perf_counter()
        self._record_refresh_metric(outcome="attempt")
        try:
            changed = await self._refresh_state_apply(
                cell,
                fingerprint,
                worktree_submodules=worktree_submodules,
            )
            self._record_refresh_metric(
                outcome="success",
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            return changed
        except WorktreeRefreshError as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            self._record_refresh_metric(
                outcome="failure",
                duration_ms=duration_ms,
                cell=cell,
                error_kind=exc.kind,
                command=exc.command,
            )
            self._log_refresh_issue(
                cell,
                exc.kind,
                str(exc),
                command=exc.command,
            )
            return False
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            self._record_refresh_metric(
                outcome="failure",
                duration_ms=duration_ms,
                cell=cell,
                error_kind="exception",
            )
            self._log_refresh_issue(cell, "exception", str(exc))
            return False

    async def _refresh_state_apply(self, cell, fingerprint: tuple,
                                   worktree_submodules=None) -> bool:
        """Apply a successful refresh to the cell, preserving healthy semantics."""
        # Three consolidated git invocations replace the previous six:
        #   - rev-list --left-right --count → ahead + behind
        #   - status --porcelain=v2         → dirty + uncommitted/untracked
        #   - diff --numstat                → committed file list + stats
        ahead, behind = await self._ahead_behind(
            cell,
            worktree_submodules=worktree_submodules,
        )
        dirty, uncommitted_files, untracked_files = await self._status_v2(
            cell,
            worktree_submodules=worktree_submodules,
        )
        diff_stats, committed_files = await self._diff_numstat(
            cell,
            worktree_submodules=worktree_submodules,
        )
        # `is_merged` can fan out to several git calls (squash detection).
        # A branch can only become "newly merged" if base has advanced past
        # the fork point — so skip the probe when behind == 0 and we
        # already knew it wasn't merged. Once True, stay True (idempotent).
        if cell.worktree_merged:
            merged = True
        elif behind == 0:
            merged = False
        else:
            try:
                merged = await asyncio.wait_for(
                    self.is_merged(
                        cell,
                        worktree_submodules=worktree_submodules,
                    ),
                    timeout=max(
                        self.refresh_git_timeout_seconds,
                        self.refresh_git_timeout_seconds * 3,
                    ),
                )
            except asyncio.TimeoutError as exc:
                raise WorktreeRefreshError(
                    "timeout",
                    "is_merged refresh probe exceeded timeout",
                    command="is_merged",
                ) from exc
            except Exception as exc:
                raise WorktreeRefreshError(
                    "git_failed",
                    str(exc) or "is_merged refresh probe failed",
                    command="is_merged",
                ) from exc

        all_changed = sorted(
            set(committed_files) | set(uncommitted_files) | set(untracked_files)
        )

        changed = False
        if diff_stats != cell.worktree_diff:
            cell.worktree_diff = diff_stats
            changed = True
        if all_changed != cell.worktree_changed_files:
            cell.worktree_changed_files = all_changed
            changed = True
        if dirty != cell.worktree_dirty:
            cell.worktree_dirty = dirty
            changed = True
        if ahead != cell.worktree_checkpoints:
            cell.worktree_checkpoints = ahead
            changed = True
        if ahead != cell.worktree_ahead:
            cell.worktree_ahead = ahead
            changed = True
        if behind != cell.worktree_behind:
            cell.worktree_behind = behind
            changed = True
        if merged != cell.worktree_merged:
            cell.worktree_merged = merged
            changed = True

        # Re-read the fingerprint after the git work — the index can be
        # touched by the diff/status calls themselves on some setups, and
        # we want the next tick to compare against the post-work state.
        self._refresh_fingerprints[cell.id] = self._refresh_fingerprint(
            cell,
            worktree_submodules,
        )
        return changed

    def forget_refresh_state(self, cell_id: str) -> None:
        """Drop the cached refresh fingerprint when an agent goes away."""
        self._refresh_fingerprints.pop(cell_id, None)

    async def _ahead_behind(self, cell,
                            worktree_submodules=None) -> tuple[int, int]:
        """One git call: returns (ahead, behind) commits vs base."""
        ahead = 0
        behind = 0
        _code, stdout, _stderr = await self._refresh_git(
            cell.worktree_path,
            "rev-list", "--left-right", "--count",
            f"{cell.worktree_base_branch}...HEAD",
        )
        parts = stdout.split()
        if len(parts) >= 2:
            ahead = int(parts[1])
            behind = int(parts[0])

        submodule_paths = _normalize_worktree_submodules(worktree_submodules)
        if not submodule_paths:
            return (ahead, behind)
        repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path) or ""
        if not repo_root:
            return (ahead, behind)
        infos = await self._nested_submodule_infos(
            repo_root,
            cell.worktree_path,
            submodule_paths,
            ref="HEAD",
            require_worktree=True,
            strict=False,
        )
        base = str(getattr(cell, "worktree_base_branch", "") or "").strip()
        for info in infos:
            _code, stdout, _stderr = await self._refresh_git(
                info["worktree_path"],
                "rev-list", "--left-right", "--count",
                f"{base}...HEAD",
            )
            parts = stdout.split()
            if len(parts) >= 2:
                ahead += int(parts[1])
                behind += int(parts[0])
        return (ahead, behind)

    async def _status_v2(self, cell,
                         worktree_submodules=None) -> tuple[bool, list[str], list[str]]:
        """One git call: returns (dirty, uncommitted_paths, untracked_paths)."""
        _code, stdout, _stderr = await self._refresh_git(
            cell.worktree_path,
            "status", "--porcelain=v2", "--untracked-files=normal",
        )
        uncommitted: list[str] = []
        untracked: list[str] = []
        dirty = False
        ignored_submodule_drift: list[str] = []
        submodule_paths = set(_normalize_worktree_submodules(
            worktree_submodules
        ))
        for raw in stdout.splitlines():
            if not raw:
                continue
            dirty = True
            tag = raw[0]
            if tag == "1":
                # ordinary changed entry: "1 XY ... <path>"
                parts = raw.split(" ", 8)
                if len(parts) >= 9:
                    path = parts[8]
                    if (
                            submodule_paths
                            and await self._is_clean_submodule_gitlink_drift(
                                cell,
                                path,
                                submodule_paths,
                                status_xy=parts[1] if len(parts) > 1 else "",
                            )):
                        ignored_submodule_drift.append(path)
                    else:
                        uncommitted.append(path)
            elif tag == "2":
                # rename/copy: "2 XY ... <path>\t<orig>"
                parts = raw.split(" ", 9)
                if len(parts) >= 10:
                    path_field = parts[9].split("\t", 1)[0]
                    uncommitted.append(path_field)
            elif tag == "?":
                # untracked: "? <path>"
                untracked.append(raw[2:])
        if dirty and not uncommitted and not untracked \
                and ignored_submodule_drift:
            dirty = False
        return (dirty, uncommitted, untracked)

    async def _diff_numstat(self, cell,
                            worktree_submodules=None) -> tuple[dict, list[str]]:
        """One git call: returns (diff_summary_dict, committed_paths)."""
        _code, numstat_text, _stderr = await self._refresh_git(
            cell.worktree_path,
            "diff", "--numstat",
            f"{cell.worktree_base_branch}...HEAD",
        )
        submodule_text = ""
        replaced_paths: set[str] = set()
        if _normalize_worktree_submodules(worktree_submodules):
            submodule_text, replaced_paths = (
                await self._nested_submodule_numstat(
                    cell,
                    worktree_submodules,
                )
            )
        if submodule_text:
            numstat_text = self._filter_numstat_paths(
                numstat_text,
                replaced_paths,
            )
            if numstat_text.strip():
                numstat_text = f"{numstat_text.rstrip()}\n{submodule_text}"
            else:
                numstat_text = submodule_text
        return _numstat_summary(numstat_text)

    async def rev_parse(self, directory: str, ref: str) -> Optional[str]:
        """Resolve a git ref or object to a full SHA."""
        if not directory or not ref:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", directory, "rev-parse", ref,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None
            sha = stdout.decode().strip()
            return sha or None
        except Exception:
            log.debug("rev_parse failed for %s @ %s", directory, ref)
            return None

    async def _git_stdout(self, directory: str, *args: str) -> tuple[int, str]:
        """Run git in *directory* and return ``(returncode, stdout_text)``."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", directory, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            return proc.returncode, stdout.decode().strip()
        except Exception:
            log.debug("git command failed for %s: %s", directory, " ".join(args))
            return 1, ""

    async def _git_run(self, directory: str,
                       *args: str) -> tuple[int, str, str]:
        """Run git in *directory* and return ``(returncode, stdout, stderr)``."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", directory, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return (
                proc.returncode,
                stdout.decode(errors="replace").strip(),
                stderr.decode(errors="replace").strip(),
            )
        except Exception as exc:
            log.debug("git command failed for %s: %s", directory, " ".join(args))
            return 1, "", str(exc)

    async def _git_common_dir(self, directory: str) -> str:
        code, stdout = await self._git_stdout(
            directory,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        )
        if code != 0:
            return ""
        return stdout.splitlines()[0].strip() if stdout else ""

    async def _is_git_repo(self, directory: str) -> bool:
        if not directory or not os.path.exists(directory):
            return False
        code, _stdout = await self._git_stdout(directory, "rev-parse", "--git-dir")
        return code == 0

    def _nested_submodule_gitdir_path(self, sub_wt_path: str) -> str:
        """Return the local gitdir for a submodule checkout without walking up.

        ``git -C <empty-submodule-dir> rev-parse`` resolves against the parent
        superproject.  For nested submodule worktree discovery we only want
        git metadata that is anchored at the submodule path itself.
        """
        if not sub_wt_path:
            return ""
        dot_git = os.path.join(sub_wt_path, ".git")
        if os.path.isdir(dot_git):
            return os.path.realpath(dot_git)
        if not os.path.isfile(dot_git):
            return ""
        try:
            with open(dot_git, encoding="utf-8") as f:
                first_line = f.readline().strip()
        except OSError:
            return ""
        prefix = "gitdir:"
        if not first_line.lower().startswith(prefix):
            return ""
        return self._resolve_path_from_config_base(
            sub_wt_path,
            first_line[len(prefix):].strip(),
        )

    async def _is_nested_submodule_linked_worktree(
            self,
            module_dir: str,
            sub_wt_path: str) -> bool:
        """Return True only for a registered linked submodule worktree.

        Empty/uninitialized submodule directories inside a superproject worktree
        must not be treated as git repositories: git would otherwise walk up to
        the superproject and report the wrong HEAD.
        """
        module_dir = os.path.realpath(os.path.expanduser(module_dir or ""))
        sub_wt_path = os.path.realpath(os.path.expanduser(sub_wt_path or ""))
        if not module_dir or not sub_wt_path or not os.path.isdir(sub_wt_path):
            return False
        gitdir = self._nested_submodule_gitdir_path(sub_wt_path)
        if not gitdir:
            return False
        worktrees_dir = os.path.join(module_dir, "worktrees")
        try:
            if os.path.commonpath([worktrees_dir, gitdir]) == worktrees_dir:
                return True
        except ValueError:
            return False
        entries = await self.list_worktrees(module_dir)
        return any(
            self._same_worktree_path(str(entry.get("path", "") or ""),
                                     sub_wt_path)
            for entry in entries
        )

    @staticmethod
    def _join_repo_rel(root: str, rel_path: str) -> str:
        root = os.path.realpath(os.path.expanduser(root or ""))
        parts = [part for part in _normalize_repo_rel_path(rel_path).split("/")
                 if part]
        return os.path.realpath(os.path.join(root, *parts))

    async def _submodule_name_for_path(self, super_wt: str,
                                       submodule_path: str) -> str:
        """Return the .gitmodules name for *submodule_path*, or the path."""
        wanted = _normalize_repo_rel_path(submodule_path)
        code, stdout = await self._git_stdout(
            super_wt,
            "config",
            "--file",
            ".gitmodules",
            "--get-regexp",
            r"^submodule\..*\.path$",
        )
        if code == 0:
            for line in stdout.splitlines():
                key, sep, value = line.partition(" ")
                if not sep:
                    continue
                if _normalize_repo_rel_path(value) != wanted:
                    continue
                prefix = "submodule."
                suffix = ".path"
                if key.startswith(prefix) and key.endswith(suffix):
                    return key[len(prefix):-len(suffix)] or wanted
        return wanted

    async def _submodule_module_dir(self, repo_root: str, super_wt: str,
                                    submodule_path: str) -> str:
        common_dir = await self._git_common_dir(repo_root)
        if not common_dir:
            return ""
        name = await self._submodule_name_for_path(super_wt, submodule_path)
        parts = [
            part for part in _normalize_repo_rel_path(name).split("/")
            if part
        ]
        if not parts:
            parts = [
                part for part in _normalize_repo_rel_path(submodule_path).split("/")
                if part
            ]
        return os.path.realpath(os.path.join(common_dir, "modules", *parts))

    @staticmethod
    def _resolve_path_from_config_base(base_dir: str, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        path = os.path.expanduser(value)
        if not os.path.isabs(path):
            path = os.path.join(base_dir, path)
        return os.path.realpath(path)

    async def _git_config_file_get(self, config_path: str,
                                   key: str) -> tuple[int, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "config", "--file", config_path, "--get", key,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _stderr = await proc.communicate()
            return proc.returncode, stdout.decode(errors="replace").strip()
        except Exception:
            log.debug("git config --file get failed for %s", config_path)
            return 1, ""

    async def _git_config_file_set(self, config_path: str, key: str,
                                   value: str) -> tuple[int, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "config", "--file", config_path, key, value,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()
            return (
                proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
        except Exception as exc:
            log.debug("git config --file set failed for %s", config_path)
            return 1, str(exc)

    async def _ensure_submodule_module_core_worktree(
            self,
            repo_root: str,
            module_dir: str,
            submodule_path: str) -> dict:
        """Keep a submodule's shared module config pinned to the main checkout.

        Git stores submodule linked worktrees below ``.git/modules/<name>``.
        Commands such as ``git submodule update --init`` invoked from a worker
        superproject checkout can rewrite the module-level ``core.worktree`` to
        that transient worker checkout.  If that worker is later removed, every
        future submodule command can fail while trying to chdir into the
        dangling path.  The shared config must instead keep pointing at the
        superproject's main submodule checkout; linked worker worktrees carry
        their own gitdir metadata under ``worktrees/``.
        """
        module_dir = os.path.realpath(os.path.expanduser(module_dir or ""))
        repo_root = os.path.realpath(os.path.expanduser(repo_root or ""))
        path = _normalize_repo_rel_path(submodule_path)
        main_worktree = self._join_repo_rel(repo_root, path)
        result = {
            "ok": False,
            "changed": False,
            "module_dir": module_dir,
            "config_path": "",
            "expected_worktree": main_worktree,
            "expected_value": "",
            "previous_value": "",
            "previous_resolved": "",
            "error": "",
        }
        if not module_dir or not repo_root or not path:
            result["error"] = "missing module_dir, repo_root, or submodule path"
            return result
        config_path = os.path.join(module_dir, "config")
        result["config_path"] = config_path
        if not os.path.isfile(config_path):
            result["error"] = f"missing submodule module config: {config_path}"
            return result

        expected_value = os.path.relpath(main_worktree, module_dir)
        expected_value = expected_value.replace(os.sep, "/")
        result["expected_value"] = expected_value

        code, current = await self._git_config_file_get(
            config_path,
            "core.worktree",
        )
        if code == 0:
            result["previous_value"] = current
            result["previous_resolved"] = self._resolve_path_from_config_base(
                module_dir,
                current,
            )
            if result["previous_resolved"] == main_worktree:
                result["ok"] = True
                return result

        set_code, err = await self._git_config_file_set(
            config_path,
            "core.worktree",
            expected_value,
        )
        if set_code != 0:
            result["error"] = err or "could not set core.worktree"
            return result
        result["ok"] = True
        result["changed"] = True
        if result["previous_resolved"] and result["previous_resolved"] != main_worktree:
            log.warning(
                "Repaired submodule module core.worktree for %s: %s -> %s",
                path,
                result["previous_resolved"],
                main_worktree,
            )
        return result

    async def _ensure_submodule_module_core_worktree_for_info(
            self,
            info: dict) -> dict:
        return await self._ensure_submodule_module_core_worktree(
            info.get("repo_root", ""),
            info.get("module_dir", ""),
            info.get("path", ""),
        )

    async def _gitlink_sha(self, super_wt: str, ref: str,
                           submodule_path: str) -> str:
        """Return the gitlink SHA for a submodule path at *ref*."""
        path = _normalize_repo_rel_path(submodule_path)
        if not super_wt or not ref or not path:
            return ""
        code, stdout = await self._git_stdout(
            super_wt,
            "ls-tree",
            ref,
            "--",
            path,
        )
        if code != 0 or not stdout:
            return ""
        line = stdout.splitlines()[0].strip()
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "160000":
            return parts[2]
        return ""

    async def _nested_submodule_infos(self, repo_root: str, super_wt: str,
                                      worktree_submodules,
                                      *,
                                      ref: str = "HEAD",
                                      require_worktree: bool = False,
                                      strict: bool = False) -> list[dict]:
        """Resolve configured submodules that are present in the super worktree."""
        infos: list[dict] = []
        for sub_path in _normalize_worktree_submodules(worktree_submodules):
            gitlink_sha = await self._gitlink_sha(super_wt, ref, sub_path)
            if not gitlink_sha:
                continue
            module_dir = await self._submodule_module_dir(
                repo_root,
                super_wt,
                sub_path,
            )
            sub_wt_path = self._join_repo_rel(super_wt, sub_path)
            if require_worktree and not (
                await self._is_nested_submodule_linked_worktree(
                    module_dir,
                    sub_wt_path,
                )
            ):
                continue
            await self._ensure_submodule_module_core_worktree(
                repo_root,
                module_dir,
                sub_path,
            )
            module_ok = await self._is_git_repo(module_dir)
            if not module_ok:
                message = (
                    f"Configured submodule '{sub_path}' is present but "
                    f"shared module store is missing: {module_dir}"
                )
                if strict:
                    raise RuntimeError(message)
                log.warning(message)
                continue
            infos.append({
                "path": sub_path,
                "repo_root": os.path.realpath(os.path.expanduser(repo_root)),
                "worktree_path": sub_wt_path,
                "module_dir": module_dir,
                "gitlink_sha": gitlink_sha,
            })
        return infos

    async def _nested_submodule_infos_for_cell(self, cell,
                                               worktree_submodules,
                                               *,
                                               require_worktree: bool = True,
                                               strict: bool = False) -> list[dict]:
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths or not cell or not getattr(cell, "worktree_path", ""):
            return []
        repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path) or ""
        if not repo_root:
            return []
        return await self._nested_submodule_infos(
            repo_root,
            cell.worktree_path,
            paths,
            ref="HEAD",
            require_worktree=require_worktree,
            strict=strict,
        )

    async def _resolve_nested_submodule_branch(self, module_dir: str,
                                               submodule_path: str,
                                               super_branch: str) -> str:
        base = _nested_submodule_branch_name(submodule_path, super_branch)
        if not await self._branch_exists(module_dir, base):
            return base
        suffix = 2
        while True:
            candidate = f"{base}-{suffix}"
            if not await self._branch_exists(module_dir, candidate):
                return candidate
            suffix += 1

    async def _create_nested_submodule_worktrees(self, repo_root: str,
                                                 super_wt: str,
                                                 super_branch: str,
                                                 worktree_submodules) -> list[dict]:
        """Create linked worktrees for configured submodules in a super worktree."""
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return []

        created: list[dict] = []
        try:
            infos = await self._nested_submodule_infos(
                repo_root,
                super_wt,
                paths,
                ref="HEAD",
                strict=True,
            )
            for info in infos:
                sub_wt_path = info["worktree_path"]
                if os.path.lexists(sub_wt_path):
                    if not os.path.isdir(sub_wt_path):
                        raise RuntimeError(
                            f"Submodule path is not a directory: {sub_wt_path}"
                        )
                    if os.listdir(sub_wt_path):
                        raise RuntimeError(
                            "Submodule worktree target is not empty: "
                            f"{sub_wt_path}"
                        )
                else:
                    os.makedirs(os.path.dirname(sub_wt_path), exist_ok=True)

                sub_branch = await self._resolve_nested_submodule_branch(
                    info["module_dir"],
                    info["path"],
                    super_branch,
                )
                await self._ensure_submodule_module_core_worktree_for_info(info)
                cmd = [
                    "git", "-C", info["module_dir"],
                    "worktree", "add",
                    "-b", sub_branch,
                    sub_wt_path,
                    info["gitlink_sha"],
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                entry = {
                    **info,
                    "branch": sub_branch,
                    "git_returncode": proc.returncode,
                    "git_stdout": stdout.decode(errors="replace").strip(),
                    "git_stderr": stderr.decode(errors="replace").strip(),
                }
                entry["module_core_worktree_after_add"] = (
                    await self._ensure_submodule_module_core_worktree_for_info(info)
                )
                if proc.returncode != 0:
                    raise RuntimeError(
                        "git submodule worktree add failed for "
                        f"{info['path']}: {entry['git_stderr']}"
                    )
                created.append(entry)
                if not entry["module_core_worktree_after_add"].get("ok"):
                    raise RuntimeError(
                        "git submodule worktree add left shared "
                        f"core.worktree invalid for {info['path']}: "
                        f"{entry['module_core_worktree_after_add'].get('error', '')}"
                    )
                code, _out, err = await self._git_run(
                    sub_wt_path,
                    "reset",
                    "--hard",
                    info["gitlink_sha"],
                )
                entry["pinned_gitlink_sha"] = info["gitlink_sha"]
                entry["pin_returncode"] = code
                if code != 0:
                    raise RuntimeError(
                        "git submodule worktree pin failed for "
                        f"{info['path']} at {info['gitlink_sha'][:12]}: {err}"
                    )
                entry["module_core_worktree_after_pin"] = (
                    await self._ensure_submodule_module_core_worktree_for_info(info)
                )
                if not entry["module_core_worktree_after_pin"].get("ok"):
                    raise RuntimeError(
                        "git submodule worktree pin left shared "
                        f"core.worktree invalid for {info['path']}: "
                        f"{entry['module_core_worktree_after_pin'].get('error', '')}"
                    )
                log.info(
                    "Created nested submodule worktree %s on %s",
                    sub_wt_path,
                    sub_branch,
                )
        except Exception:
            for entry in reversed(created):
                try:
                    await self._remove_one_nested_submodule_worktree(
                        entry,
                        force=True,
                    )
                except Exception:
                    log.debug(
                        "Failed to clean up nested submodule worktree %s",
                        entry.get("worktree_path"),
                        exc_info=True,
                    )
            raise

        return created

    async def _cat_file_commit_exists(self, directory: str, sha: str) -> bool:
        if not directory or not sha:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", directory, "cat-file", "-e", f"{sha}^{{commit}}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    async def _create_preserved_nested_submodule_ref(self, info: dict,
                                                     head: str = "") -> dict:
        """Create a stable branch ref for a nested-submodule HEAD."""
        module_dir = info.get("module_dir", "")
        head = (
            head
            or await self.rev_parse(info.get("worktree_path", ""), "HEAD")
            or ""
        )
        result = {
            "head": head,
            "branch": "",
            "branch_created": False,
        }
        if not module_dir or not head:
            return result
        base = (
            f"torque/preserved/"
            f"{_slugify_worktree_name(info.get('path', '').replace('/', '-'), max_len=60) or 'submodule'}/"
            f"{head[:12] or 'head'}"
        )
        preserve_branch = base
        suffix = 2
        while await self._branch_exists(module_dir, preserve_branch):
            existing = await self.rev_parse(module_dir, preserve_branch) or ""
            if existing == head:
                result["branch"] = preserve_branch
                return result
            preserve_branch = f"{base}-{suffix}"
            suffix += 1
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", module_dir, "branch", preserve_branch, head,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                "Could not preserve nested submodule HEAD "
                f"{head[:12]}: {stderr.decode(errors='replace').strip()}"
            )
        result.update({
            "branch": preserve_branch,
            "branch_created": True,
        })
        return result

    async def _preserve_nested_submodule_head(self, info: dict) -> dict:
        """Ensure the nested submodule HEAD remains reachable by a branch ref."""
        sub_wt_path = info.get("worktree_path", "")
        module_dir = info.get("module_dir", "")
        head = await self.rev_parse(sub_wt_path, "HEAD") or ""
        branch = (await self.get_current_branch(sub_wt_path) or "").strip()
        created_branch = False
        preserve_branch = branch if branch and branch != "HEAD" else ""
        if not preserve_branch or not await self._branch_exists(
                module_dir, preserve_branch):
            preserved = await self._create_preserved_nested_submodule_ref(
                info,
                head,
            )
            preserve_branch = preserved.get("branch", "")
            created_branch = bool(preserved.get("branch_created"))

        return {
            "head": head,
            "branch": preserve_branch,
            "branch_created": created_branch,
            "head_reachable_before_remove": (
                await self._cat_file_commit_exists(module_dir, head)
            ),
        }

    async def _remove_one_nested_submodule_worktree(self, info: dict, *,
                                                   force: bool = True) -> dict:
        """Remove one linked submodule worktree while preserving its branch ref."""
        sub_wt_path = info.get("worktree_path", "")
        module_dir = info.get("module_dir", "")
        result = {
            "path": info.get("path", ""),
            "worktree_path": sub_wt_path,
            "module_dir": module_dir,
            "ok": False,
            "worktree_removed": False,
            "branch_preserved": False,
            "branch": "",
            "head": "",
            "git_returncode": None,
            "git_stdout": "",
            "git_stderr": "",
            "pre_state": {},
            "post_state": {},
            "module_core_worktree": {},
            "message": "",
        }
        if info.get("repo_root"):
            result["module_core_worktree"] = (
                await self._ensure_submodule_module_core_worktree_for_info(info)
            )
        guard = await self._preserve_nested_submodule_head(info)
        result.update({
            "branch": guard.get("branch", ""),
            "head": guard.get("head", ""),
            "branch_created": guard.get("branch_created", False),
            "head_reachable_before_remove": guard.get(
                "head_reachable_before_remove", False),
        })
        result["pre_state"] = await self.removal_state(
            module_dir,
            sub_wt_path,
            branch=result["branch"],
        )

        try:
            remove_cwd = module_dir or sub_wt_path
            cmd = [
                "git", "-C", remove_cwd,
                "worktree", "remove", sub_wt_path,
            ]
            if force:
                cmd.append("--force")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            result["git_returncode"] = proc.returncode
            result["git_stdout"] = stdout.decode(errors="replace").strip()
            result["git_stderr"] = stderr.decode(errors="replace").strip()
        except Exception:
            log.exception("Failed to remove nested submodule worktree %s",
                          sub_wt_path)
            result["git_returncode"] = -1

        if info.get("repo_root"):
            result["module_core_worktree"] = (
                await self._ensure_submodule_module_core_worktree_for_info(info)
            )
        result["post_state"] = await self.removal_state(
            module_dir,
            sub_wt_path,
            branch=result["branch"],
        )
        result["worktree_removed"] = (
            not bool(result["post_state"].get("path_exists"))
            and not bool(result["post_state"].get("listed"))
        )
        branch_exists = bool(result["post_state"].get("branch_exists"))
        head_reachable = await self._cat_file_commit_exists(
            module_dir,
            result["head"],
        )
        result["head_reachable_after_remove"] = head_reachable
        result["branch_preserved"] = bool(branch_exists and head_reachable)
        result["ok"] = bool(
            result["worktree_removed"] and result["branch_preserved"]
            and (
                not result["module_core_worktree"]
                or result["module_core_worktree"].get("ok")
            )
        )
        result["message"] = (
            "Nested submodule worktree removed and branch preserved"
            if result["ok"]
            else "Nested submodule worktree removal did not preserve expected state"
        )
        return result

    async def _remove_nested_submodule_worktrees(self, repo_root: str,
                                                super_wt: str,
                                                worktree_submodules,
                                                *,
                                                force: bool = True) -> list[dict]:
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return []
        infos = await self._nested_submodule_infos(
            repo_root,
            super_wt,
            paths,
            ref="HEAD",
            require_worktree=True,
            strict=False,
        )
        removed = []
        for info in infos:
            removed.append(
                await self._remove_one_nested_submodule_worktree(
                    info,
                    force=force,
                )
            )
        return removed

    def _filter_numstat_paths(self, text: str,
                              skip_paths: set[str]) -> str:
        if not skip_paths:
            return text
        lines = []
        for line in str(text or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and _normalize_repo_rel_path(parts[2]) in skip_paths:
                continue
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _prefix_numstat_paths(text: str, prefix: str) -> str:
        prefix = _normalize_repo_rel_path(prefix)
        lines = []
        for line in str(text or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            parts[2] = f"{prefix}/{parts[2]}" if prefix else parts[2]
            lines.append("\t".join(parts))
        return "\n".join(lines)

    async def _nested_submodule_numstat(self, cell,
                                        worktree_submodules) -> tuple[str, set[str]]:
        """Return prefixed numstat text for configured nested submodule worktrees."""
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return "", set()
        repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path) or ""
        if not repo_root:
            return "", set()
        infos = await self._nested_submodule_infos(
            repo_root,
            cell.worktree_path,
            paths,
            ref="HEAD",
            require_worktree=True,
            strict=False,
        )
        chunks: list[str] = []
        replaced: set[str] = set()
        for info in infos:
            base_sha = await self._gitlink_sha(
                cell.worktree_path,
                cell.worktree_base_branch,
                info["path"],
            )
            args = ["diff", "--numstat"]
            if base_sha:
                # Compare the current nested worktree state (committed and
                # tracked uncommitted edits) against the superproject base
                # gitlink. This replaces the superproject's coarse gitlink
                # numstat with file-level submodule stats.
                args.append(base_sha)
            _code, stdout, _stderr = await self._refresh_git(
                info["worktree_path"],
                *args,
            )
            if not stdout:
                continue
            prefixed = self._prefix_numstat_paths(stdout, info["path"])
            if prefixed:
                chunks.append(prefixed)
                replaced.add(info["path"])
        return "\n".join(chunks), replaced

    async def nested_submodule_head_states(self, cell,
                                           worktree_submodules) -> list[dict]:
        """Return current branch/head/gitlink metadata for configured submodules."""
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return []
        repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
        if not repo_root and getattr(cell, "worktree_path", ""):
            repo_root = await self.get_repo_root(cell.worktree_path) or ""
        if not repo_root:
            return []
        infos = await self._nested_submodule_infos(
            repo_root,
            getattr(cell, "worktree_path", "") or "",
            paths,
            ref="HEAD",
            require_worktree=True,
            strict=False,
        )
        states: list[dict] = []
        base = str(getattr(cell, "worktree_base_branch", "") or "").strip()
        for info in infos:
            sub_wt = info.get("worktree_path", "")
            branch = await self.get_current_branch(sub_wt)
            head = await self.rev_parse(sub_wt, "HEAD") or ""
            states.append({
                "path": info.get("path", ""),
                "repo_root": info.get("module_dir", ""),
                "branch": branch if branch != "HEAD" else "",
                "base_branch": base,
                "commit_sha": head,
                "gitlink_sha": info.get("gitlink_sha", ""),
            })
        return states

    async def gitlink_reconciliation_boundary_state(
            self,
            cell,
            *,
            boundary_commit_sha: str,
            head_sha: str,
            recorded_submodules: list[dict] | None = None,
            current_submodules: list[dict] | None = None,
            worktree_submodules=None,
    ) -> dict:
        """Validate a branch-tip move as a pure nested-gitlink reconciliation.

        Review boundaries normally require the worktree branch tip to stay
        fixed.  The one safe exception is Torque's merge-time nested submodule
        reconciliation: the superproject branch gains only gitlink bumps, and
        each bumped submodule commit is the clean merge of the reviewed nested
        branch tip with the already-landed base submodule tip.
        """
        repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
        if not repo_root and getattr(cell, "worktree_path", ""):
            repo_root = await self.get_repo_root(cell.worktree_path) or ""
        wt_dir = str(getattr(cell, "worktree_path", "") or "").strip()
        base = str(getattr(cell, "worktree_base_branch", "") or "").strip()
        boundary_commit_sha = str(boundary_commit_sha or "").strip()
        head_sha = str(head_sha or "").strip()
        if not repo_root or not wt_dir or not base:
            return {"ok": False, "reason": "missing_worktree"}
        if not boundary_commit_sha or not head_sha:
            return {"ok": False, "reason": "missing_commit"}
        if boundary_commit_sha == head_sha:
            return {"ok": False, "reason": "unchanged"}

        code, _out = await self._git_stdout(
            repo_root,
            "merge-base",
            "--is-ancestor",
            boundary_commit_sha,
            head_sha,
        )
        if code != 0:
            return {"ok": False, "reason": "boundary_not_ancestor"}

        configured_paths = set(_normalize_worktree_submodules(worktree_submodules))
        recorded_by_path = {
            _normalize_repo_rel_path(item.get("path", "")): item
            for item in (recorded_submodules or [])
            if isinstance(item, dict) and _normalize_repo_rel_path(
                item.get("path", "")
            )
        }
        current_by_path = {
            _normalize_repo_rel_path(item.get("path", "")): item
            for item in (current_submodules or [])
            if isinstance(item, dict) and _normalize_repo_rel_path(
                item.get("path", "")
            )
        }
        allowed_paths = configured_paths | set(recorded_by_path)
        if not allowed_paths:
            return {"ok": False, "reason": "missing_submodules"}

        changed_paths = await self._diff_name_only(
            repo_root,
            boundary_commit_sha,
            head_sha,
        )
        if not changed_paths:
            return {"ok": False, "reason": "no_changed_paths"}
        unexpected = [path for path in changed_paths if path not in allowed_paths]
        if unexpected:
            return {
                "ok": False,
                "reason": "non_gitlink_changes",
                "paths": changed_paths,
                "unexpected_paths": unexpected,
            }

        changed_by_commit = await self._gitlink_reconciliation_commit_paths(
            repo_root,
            boundary_commit_sha,
            head_sha,
        )
        unexpected_commits = [
            item for item in changed_by_commit
            if any(path not in allowed_paths for path in item.get("paths", []))
        ]
        if unexpected_commits:
            return {
                "ok": False,
                "reason": "non_gitlink_commit",
                "commits": unexpected_commits,
            }

        reconciled: list[dict] = []
        for path in changed_paths:
            recorded = recorded_by_path.get(path, {})
            current = current_by_path.get(path, {})
            module_dir = (
                str(current.get("repo_root", "") or "").strip()
                or str(recorded.get("repo_root", "") or "").strip()
            )
            old_gitlink = await self._gitlink_sha(wt_dir, boundary_commit_sha, path)
            new_gitlink = await self._gitlink_sha(wt_dir, head_sha, path)
            base_gitlink = await self._gitlink_sha(wt_dir, base, path)
            recorded_sha = str(
                recorded.get("commit_sha", "")
                or recorded.get("head_sha", "")
                or old_gitlink
            ).strip()
            current_sha = str(
                current.get("commit_sha", "")
                or current.get("head_sha", "")
                or new_gitlink
            ).strip()
            if not module_dir:
                sub_wt = self._join_repo_rel(wt_dir, path)
                module_dir = await self.get_repo_root(sub_wt) or ""
            if not old_gitlink or not new_gitlink or not module_dir:
                return {
                    "ok": False,
                    "reason": "missing_gitlink",
                    "path": path,
                }
            if current_sha and current_sha != new_gitlink:
                return {
                    "ok": False,
                    "reason": "head_gitlink_mismatch",
                    "path": path,
                    "current_sha": current_sha,
                    "new_gitlink_sha": new_gitlink,
                }
            if recorded_sha and old_gitlink and recorded_sha != old_gitlink:
                return {
                    "ok": False,
                    "reason": "recorded_gitlink_mismatch",
                    "path": path,
                    "recorded_sha": recorded_sha,
                    "old_gitlink_sha": old_gitlink,
                }
            if not await self._commit_is_ancestor(
                    module_dir,
                    old_gitlink,
                    new_gitlink,
            ):
                return {
                    "ok": False,
                    "reason": "reviewed_submodule_not_ancestor",
                    "path": path,
                }
            if (
                base_gitlink
                and base_gitlink != old_gitlink
                and not await self._commit_is_ancestor(
                    module_dir,
                    base_gitlink,
                    new_gitlink,
                )
            ):
                return {
                    "ok": False,
                    "reason": "base_submodule_not_ancestor",
                    "path": path,
                }
            if (
                base_gitlink
                and not await self._merge_tree_matches_commit(
                    module_dir,
                    base_gitlink,
                    old_gitlink,
                    new_gitlink,
                )
            ):
                return {
                    "ok": False,
                    "reason": "submodule_tree_not_clean_merge",
                    "path": path,
                }
            reconciled.append({
                "path": path,
                "old_gitlink_sha": old_gitlink,
                "new_gitlink_sha": new_gitlink,
                "base_gitlink_sha": base_gitlink,
            })

        for path, recorded in recorded_by_path.items():
            if path in changed_paths:
                continue
            current = current_by_path.get(path)
            if not current:
                return {
                    "ok": False,
                    "reason": "missing_current_submodule",
                    "path": path,
                }
            recorded_sha = str(
                recorded.get("commit_sha", "")
                or recorded.get("head_sha", "")
            ).strip()
            current_sha = str(
                current.get("commit_sha", "")
                or current.get("head_sha", "")
            ).strip()
            if recorded_sha and current_sha and recorded_sha != current_sha:
                return {
                    "ok": False,
                    "reason": "unrelated_submodule_tip_moved",
                    "path": path,
                    "recorded_sha": recorded_sha,
                    "current_sha": current_sha,
                }

        return {
            "ok": True,
            "reason": "gitlink_reconciliation",
            "paths": changed_paths,
            "submodules": reconciled,
            "commits": changed_by_commit,
        }

    async def _gitlink_reconciliation_commit_paths(
            self,
            repo_root: str,
            old_ref: str,
            new_ref: str) -> list[dict]:
        code, stdout = await self._git_stdout(
            repo_root,
            "rev-list",
            "--reverse",
            f"{old_ref}..{new_ref}",
        )
        if code != 0:
            return []
        commits = [line.strip() for line in stdout.splitlines() if line.strip()]
        out: list[dict] = []
        for commit in commits:
            path_code, path_out = await self._git_stdout(
                repo_root,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                f"{commit}^!",
            )
            if path_code != 0:
                paths = await self._diff_name_only(repo_root, f"{commit}^", commit)
            else:
                paths = [
                    _normalize_repo_rel_path(line.strip())
                    for line in path_out.splitlines()
                    if _normalize_repo_rel_path(line.strip())
                ]
            out.append({"commit_sha": commit, "paths": paths})
        return out

    async def boundary_tip_mismatch_info(self, cell, boundary_sha: str,
                                         tip_sha: str) -> dict:
        """Classify a reviewed-boundary SHA versus the current branch tip."""
        repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
        if not repo_root and getattr(cell, "worktree_path", ""):
            repo_root = await self.get_repo_root(cell.worktree_path) or ""
        boundary_sha = str(boundary_sha or "").strip()
        tip_sha = str(tip_sha or "").strip()
        info = {
            "boundary_sha": boundary_sha,
            "tip_sha": tip_sha,
            "classification": "unknown",
            "commit_count": 0,
        }
        if not repo_root or not boundary_sha or not tip_sha:
            info["reason"] = "missing_ref"
            return info
        if boundary_sha == tip_sha:
            info["classification"] = "same"
            info["ancestor"] = True
            return info

        code, _out = await self._git_stdout(
            repo_root,
            "merge-base",
            "--is-ancestor",
            boundary_sha,
            tip_sha,
        )
        if code == 0:
            info["classification"] = "ahead"
            info["ancestor"] = True
            count_code, count_out = await self._git_stdout(
                repo_root,
                "rev-list",
                "--count",
                f"{boundary_sha}..{tip_sha}",
            )
            if count_code == 0:
                try:
                    info["commit_count"] = int(count_out.strip() or "0")
                except ValueError:
                    info["commit_count"] = 0
            return info

        info["classification"] = "diverged"
        info["ancestor"] = False
        return info

    async def _commit_is_ancestor(self, repo_root: str, ancestor: str,
                                  descendant: str) -> bool:
        if not repo_root or not ancestor or not descendant:
            return False
        if ancestor == descendant:
            return True
        code, _out = await self._git_stdout(
            repo_root,
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        )
        return code == 0

    async def _merge_tree_matches_commit(self, repo_root: str, base_ref: str,
                                         branch_ref: str,
                                         expected_ref: str) -> bool:
        if not repo_root or not base_ref or not branch_ref or not expected_ref:
            return False
        code, tree_out, _err = await self._git_run(
            repo_root,
            "merge-tree",
            "--write-tree",
            base_ref,
            branch_ref,
        )
        if code != 0 or not tree_out:
            return False
        merge_tree = tree_out.splitlines()[0].strip()
        expected_tree = await self.rev_parse(repo_root, f"{expected_ref}^{{tree}}")
        return bool(merge_tree and expected_tree and merge_tree == expected_tree)

    async def _nested_submodule_base_gitlink(self, cell, path: str) -> str:
        base = str(getattr(cell, "worktree_base_branch", "") or "").strip()
        if not base:
            return ""
        return await self._gitlink_sha(
            getattr(cell, "worktree_path", "") or "",
            base,
            path,
        )

    async def _nested_submodule_remote_name(self, sub_wt: str) -> str:
        code, stdout = await self._git_stdout(sub_wt, "remote")
        if code != 0:
            return ""
        remotes = [line.strip() for line in stdout.splitlines() if line.strip()]
        if "origin" in remotes:
            return "origin"
        return remotes[0] if remotes else ""

    async def _nested_submodule_fetch_remote(self, sub_wt: str,
                                             remote: str) -> tuple[bool, str]:
        if not remote:
            return False, "No remote configured"
        code, _out, err = await self._git_run(sub_wt, "fetch", "--quiet", remote)
        if code != 0:
            return False, err or f"git fetch {remote} failed"
        return True, ""

    async def _remote_contains_commit(self, module_dir: str, remote: str,
                                      sha: str) -> tuple[bool, list[str]]:
        if not module_dir or not remote or not sha:
            return False, []
        code, stdout = await self._git_stdout(
            module_dir,
            "branch",
            "-r",
            "--contains",
            sha,
        )
        if code != 0:
            return False, []
        prefix = f"{remote}/"
        refs: list[str] = []
        for raw in stdout.splitlines():
            ref = raw.strip().lstrip("*").strip()
            if not ref or ref.endswith("/HEAD"):
                continue
            if ref == remote or ref.startswith(prefix):
                refs.append(ref)
        return bool(refs), refs

    async def _remote_branch_sha(self, module_dir: str, remote: str,
                                 branch: str) -> str:
        if not module_dir or not remote or not branch:
            return ""
        code, stdout = await self._git_stdout(
            module_dir,
            "rev-parse",
            "--verify",
            f"{remote}/{branch}",
        )
        if code != 0:
            return ""
        return stdout.splitlines()[0].strip() if stdout else ""

    async def _auto_publish_nested_submodule_preflight_ref(
            self,
            sub_wt: str,
            remote: str,
            entry: dict,
            *,
            reason: str,
    ) -> dict:
        branch = str(entry.get("branch", "") or "").strip()
        head = str(entry.get("head_sha", "") or "").strip()
        if not branch or not head:
            return {"ok": False, "error": "No nested branch/head to push."}

        pushed = await self._push_nested_submodule_ref(
            sub_wt,
            remote,
            head,
            branch,
        )
        if not pushed.get("ok"):
            error = pushed.get("error", "")
            entry[f"{reason}_publish_error"] = error
            if entry.get("zero_gitlink_delta"):
                entry["zero_delta_branch_publish_error"] = error
            return pushed

        entry["remote_branch_sha"] = head
        entry["branch_ref_published"] = True
        entry[f"{reason}_published"] = True
        if entry.get("zero_gitlink_delta"):
            entry["zero_delta_branch_published"] = True
        remote_ref = f"{remote}/{branch}" if remote and branch else ""
        if remote_ref:
            refs = [
                str(ref or "").strip()
                for ref in (entry.get("remote_refs_containing_gitlink") or [])
                if str(ref or "").strip()
            ]
            if remote_ref not in refs:
                refs.append(remote_ref)
            entry["remote_refs_containing_gitlink"] = refs
        return pushed

    def _nested_preflight_error(self, entry: dict, condition: str,
                                detail: str) -> dict:
        path = entry.get("path", "")
        old_sha = entry.get("old_gitlink_sha", "")
        new_sha = entry.get("new_gitlink_sha", "")
        head = entry.get("head_sha", "")
        branch = entry.get("branch", "")
        message = (
            "Nested submodule merge preflight failed for "
            f"{path}: {condition}. {detail} "
            f"old_gitlink={old_sha or '(missing)'} "
            f"new_gitlink={new_sha or '(missing)'} "
            f"head={head or '(missing)'}"
        ).strip()
        if branch:
            message += f" branch={branch}"
        return {
            "ok": False,
            "error": message,
            "condition": condition,
            "submodule": entry,
        }

    async def nested_submodule_merge_preflight(self, cell,
                                               worktree_submodules) -> dict:
        """Validate nested submodule gitlinks are safe to publish/merge.

        The checks are dormant unless configured submodule worktrees are
        present.  They deliberately fail before any superproject merge can
        publish a gitlink to an unfetchable submodule commit.
        """
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return {"ok": True, "submodules": []}
        infos = await self._nested_submodule_infos_for_cell(
            cell,
            paths,
            require_worktree=True,
            strict=False,
        )
        checked: list[dict] = []
        for info in infos:
            sub_wt = info["worktree_path"]
            path = info["path"]
            branch = await self.get_current_branch(sub_wt)
            head = await self.rev_parse(sub_wt, "HEAD") or ""
            old_gitlink = await self._nested_submodule_base_gitlink(cell, path)
            new_gitlink = info.get("gitlink_sha", "")
            zero_gitlink_delta = bool(
                old_gitlink and old_gitlink == new_gitlink == head
            )
            entry = {
                "path": path,
                "worktree_path": sub_wt,
                "repo_root": info.get("module_dir", ""),
                "branch": "" if branch == "HEAD" else branch,
                "base_branch": str(
                    getattr(cell, "worktree_base_branch", "") or ""
                ).strip(),
                "old_gitlink_sha": old_gitlink,
                "new_gitlink_sha": new_gitlink,
                "head_sha": head,
                "zero_gitlink_delta": zero_gitlink_delta,
                "remote": "",
                "remote_refs_containing_gitlink": [],
            }

            code, status = await self._git_stdout(
                sub_wt,
                "status",
                "--porcelain",
            )
            if code != 0:
                return self._nested_preflight_error(
                    entry,
                    "STATUS_FAILED",
                    "Could not read submodule status.",
                )
            if status.strip():
                return self._nested_preflight_error(
                    entry,
                    "DIRTY",
                    "Commit or checkpoint nested submodule changes first.",
                )

            if zero_gitlink_delta:
                checked.append({
                    **entry,
                    "skipped": True,
                    "skip_reason": "zero_gitlink_delta",
                })
                continue

            if head and new_gitlink and head != new_gitlink:
                return self._nested_preflight_error(
                    entry,
                    "HEAD_MISMATCH",
                    "Submodule HEAD does not match the superproject gitlink.",
                )

            remote = await self._nested_submodule_remote_name(sub_wt)
            entry["remote"] = remote
            fetched, fetch_error = await self._nested_submodule_fetch_remote(
                sub_wt,
                remote,
            )
            if not fetched:
                return self._nested_preflight_error(
                    entry,
                    "REMOTE_UNAVAILABLE",
                    fetch_error,
                )

            remote_branch_sha = await self._remote_branch_sha(
                info["module_dir"],
                remote,
                entry["branch"],
            )
            entry["remote_branch_sha"] = remote_branch_sha
            entry["remote_base_sha"] = await self._remote_branch_sha(
                info["module_dir"],
                remote,
                entry["base_branch"],
            )

            contains, refs = await self._remote_contains_commit(
                info["module_dir"],
                remote,
                new_gitlink,
            )
            entry["remote_refs_containing_gitlink"] = refs
            if not contains:
                can_publish_gitlink = bool(
                    entry["branch"]
                    and head
                    and new_gitlink
                    and head == new_gitlink
                )
                if can_publish_gitlink:
                    pushed = await self._auto_publish_nested_submodule_preflight_ref(
                        sub_wt,
                        remote,
                        entry,
                        reason="missing_gitlink",
                    )
                    if not pushed.get("ok"):
                        return self._nested_preflight_error(
                            entry,
                            "MISSING_FROM_REMOTE",
                            "The gitlink commit is not reachable from the "
                            f"{remote} remote, and automatic ref publish "
                            f"failed: {pushed.get('error', '')}",
                        )
                else:
                    return self._nested_preflight_error(
                        entry,
                        "MISSING_FROM_REMOTE",
                        "The gitlink commit is not reachable from the "
                        f"{remote} remote.",
                    )

            if entry["branch"] and entry.get("remote_branch_sha", "") != head:
                pushed = await self._auto_publish_nested_submodule_preflight_ref(
                    sub_wt,
                    remote,
                    entry,
                    reason="branch_tip",
                )
                if not pushed.get("ok"):
                    return self._nested_preflight_error(
                        entry,
                        "UNPUSHED",
                        "The nested submodule branch tip is not pushed to "
                        f"{remote}/{entry['branch']}: "
                        f"{pushed.get('error', '')}",
                    )

            checked.append(entry)
        return {"ok": True, "submodules": checked}

    async def _checked_out_worktree_for_branch(self, repo_root: str,
                                               branch: str) -> str:
        if not repo_root or not branch:
            return ""
        for entry in await self.list_worktrees(repo_root):
            entry_branch = str(entry.get("branch", "") or "").strip()
            branch_ref = str(entry.get("branch_ref", "") or "").strip()
            if entry_branch == branch or branch_ref == f"refs/heads/{branch}":
                return str(entry.get("path", "") or "").strip()
        current = await self.get_current_branch(repo_root)
        if current == branch:
            return repo_root
        return ""

    async def _advance_branch_to_commit(self, repo_root: str, branch: str,
                                        sha: str) -> tuple[bool, str]:
        checkout = await self._checked_out_worktree_for_branch(repo_root, branch)
        if checkout:
            code, _out, err = await self._git_run(
                checkout,
                "merge",
                "--ff-only",
                sha,
            )
            if code != 0:
                return False, err or "merge --ff-only failed"
            return True, ""
        code, _out, err = await self._git_run(
            repo_root,
            "update-ref",
            f"refs/heads/{branch}",
            sha,
        )
        if code != 0:
            return False, err or "update-ref failed"
        return True, ""

    async def _set_branch_to_commit(self, repo_root: str, branch: str,
                                    sha: str) -> tuple[bool, str]:
        """Move a local branch to *sha*, updating a checked-out worktree too."""
        checkout = await self._checked_out_worktree_for_branch(repo_root, branch)
        if checkout:
            status_code, status_out = await self._git_stdout(
                checkout,
                "status",
                "--porcelain",
            )
            if status_code != 0:
                return False, "status --porcelain failed"
            if status_out.strip():
                return (
                    False,
                    f"Cannot reset checked-out branch {branch}: "
                    "worktree has uncommitted changes.",
                )
            code, _out, err = await self._git_run(
                checkout,
                "reset",
                "--hard",
                sha,
            )
            if code != 0:
                return False, err or "reset --hard failed"
            return True, ""
        code, _out, err = await self._git_run(
            repo_root,
            "update-ref",
            f"refs/heads/{branch}",
            sha,
        )
        if code != 0:
            return False, err or "update-ref failed"
        return True, ""

    async def _sync_branch_to_remote(self, repo_root: str, *,
                                     remote: str,
                                     branch: str,
                                     force: bool = False) -> dict:
        remote_sha = await self._remote_branch_sha(repo_root, remote, branch)
        if not remote_sha:
            return {"ok": True, "changed": False, "remote_sha": ""}

        local_sha = await self.rev_parse(repo_root, branch) or ""
        if local_sha == remote_sha:
            return {
                "ok": True,
                "changed": False,
                "local_sha": local_sha,
                "remote_sha": remote_sha,
            }

        if local_sha and not force:
            code, _out = await self._git_stdout(
                repo_root,
                "merge-base",
                "--is-ancestor",
                local_sha,
                remote_sha,
            )
            if code != 0:
                return {
                    "ok": False,
                    "error": (
                        f"Local {branch} is not a fast-forward of "
                        f"{remote}/{branch}."
                    ),
                    "local_sha": local_sha,
                    "remote_sha": remote_sha,
                }

        if force:
            moved, error = await self._set_branch_to_commit(
                repo_root,
                branch,
                remote_sha,
            )
        else:
            moved, error = await self._advance_branch_to_commit(
                repo_root,
                branch,
                remote_sha,
            )
        if not moved:
            return {
                "ok": False,
                "error": error,
                "local_sha": local_sha,
                "remote_sha": remote_sha,
            }
        return {
            "ok": True,
            "changed": True,
            "local_sha": local_sha,
            "remote_sha": remote_sha,
            "forced": bool(force),
        }

    async def _merge_branch_into_base_repo(self, repo_root: str, *,
                                           base_branch: str,
                                           branch: str,
                                           message: str) -> dict:
        base_sha = await self.rev_parse(repo_root, base_branch) or ""
        branch_sha = await self.rev_parse(repo_root, branch) or ""
        if not base_sha or not branch_sha:
            return {
                "ok": False,
                "error": (
                    f"Cannot resolve {base_branch} or {branch} in nested "
                    "submodule."
                ),
            }
        if base_sha == branch_sha:
            return {"ok": True, "sha": base_sha, "changed": False}

        code, _out = await self._git_stdout(
            repo_root,
            "merge-base",
            "--is-ancestor",
            branch,
            base_branch,
        )
        if code == 0:
            return {"ok": True, "sha": base_sha, "changed": False}

        code, _out = await self._git_stdout(
            repo_root,
            "merge-base",
            "--is-ancestor",
            base_branch,
            branch,
        )
        if code == 0:
            new_sha = branch_sha
        else:
            code, tree_out, err = await self._git_run(
                repo_root,
                "merge-tree",
                "--write-tree",
                base_branch,
                branch,
            )
            if code != 0:
                conflicts = await self._parse_merge_tree_conflicts(
                    tree_out,
                    repo_root=repo_root,
                    base_label=base_branch,
                    branch_label=branch,
                )
                paths = ", ".join(
                    item.get("path", "") for item in conflicts if item.get("path")
                )
                detail = f" conflicts: {paths}" if paths else ""
                return {
                    "ok": False,
                    "error": (
                        "Nested submodule merge conflict while merging "
                        f"{branch} into {base_branch}.{detail}"
                    ) or err,
                    "conflicts": conflicts,
                }
            tree_sha = tree_out.splitlines()[0].strip() if tree_out else ""
            code, commit_out, commit_err = await self._git_run(
                repo_root,
                "commit-tree",
                tree_sha,
                "-p",
                base_sha,
                "-p",
                branch_sha,
                "-m",
                message,
            )
            if code != 0:
                return {
                    "ok": False,
                    "error": commit_err or "nested submodule commit-tree failed",
                }
            new_sha = commit_out.splitlines()[0].strip()

        advanced, advance_error = await self._advance_branch_to_commit(
            repo_root,
            base_branch,
            new_sha,
        )
        if not advanced:
            return {"ok": False, "error": advance_error}
        return {"ok": True, "sha": new_sha, "changed": new_sha != base_sha}

    async def _push_nested_submodule_ref(self, sub_wt: str, remote: str,
                                         sha: str, branch: str) -> dict:
        code, _out, err = await self._git_run(
            sub_wt,
            "push",
            remote,
            f"{sha}:refs/heads/{branch}",
        )
        if code != 0:
            return {
                "ok": False,
                "error": err or f"git push {remote} {branch} failed",
            }
        return {"ok": True}

    async def _push_nested_submodule_pr_head(self, module_dir: str,
                                             sub_wt: str, remote: str,
                                             sha: str, branch: str) -> dict:
        """Push a nested submodule branch as a PR head.

        Normal pushes are preferred.  If the remote branch is stale but safely
        behind the local reviewed head, retry with an explicit lease.  If the
        remote branch diverged, fail closed instead of overwriting another ee
        PR head.
        """
        pushed = await self._push_nested_submodule_ref(
            sub_wt,
            remote,
            sha,
            branch,
        )
        if pushed.get("ok"):
            pushed.update({
                "phase": "nested_submodule_pr_push",
                "remote": remote,
                "branch": branch,
                "pushed_sha": sha,
            })
            return pushed

        error = str(pushed.get("error") or "").strip()
        if not self._push_rejected_non_fast_forward(error):
            return _worktree_error(
                "nested_submodule_pr_push",
                error or f"git push {remote} {branch} failed",
                remote=remote,
                branch=branch,
                head_sha=sha,
                non_fast_forward=False,
            )

        remote_sha = await self._remote_branch_sha(module_dir, remote, branch)
        safe_to_force = bool(
            remote_sha
            and await self._commit_is_ancestor(module_dir, remote_sha, sha)
        )
        if not safe_to_force:
            return _worktree_error(
                "nested_submodule_pr_push",
                (
                    f"Nested submodule PR branch {remote}/{branch} diverged; "
                    "refusing to force-push because the remote head is not an "
                    "ancestor of the local reviewed head."
                ),
                remote=remote,
                branch=branch,
                head_sha=sha,
                remote_sha=remote_sha,
                non_fast_forward=True,
                safety_gate_passed=False,
            )

        code, _out, force_err = await self._git_run(
            sub_wt,
            "push",
            f"--force-with-lease=refs/heads/{branch}:{remote_sha}",
            remote,
            f"{sha}:refs/heads/{branch}",
        )
        if code != 0:
            return _worktree_error(
                "nested_submodule_pr_push",
                force_err or f"git push --force-with-lease {remote} {branch} failed",
                remote=remote,
                branch=branch,
                head_sha=sha,
                remote_sha=remote_sha,
                non_fast_forward=True,
                safety_gate_passed=True,
            )
        return _worktree_ok(
            "nested_submodule_pr_push",
            remote=remote,
            branch=branch,
            pushed_sha=sha,
            remote_sha=remote_sha,
            non_fast_forward=True,
            force_with_lease=True,
            safety_gate_passed=True,
        )

    @staticmethod
    def _push_rejected_non_fast_forward(message: str) -> bool:
        text = (message or "").lower()
        return (
            "non-fast-forward" in text
            or "fetch first" in text
            or "stale info" in text
            or "failed to push some refs" in text
        )

    async def publish_nested_submodule_branches_for_merge(
            self,
            cell,
            worktree_submodules,
    ) -> dict:
        """Publish nested submodule worktree branches before PR preflight.

        The regular nested merge preflight requires the superproject gitlink
        commit to be reachable from the nested submodule remote and from the
        worker's nested branch.  Direct merges historically relied on callers
        having pushed that branch already; the PR path owns branch publication,
        so it calls this helper before running the shared gate.
        """
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return {"ok": True, "phase": "nested_submodule_publish",
                    "submodules": []}
        infos = await self._nested_submodule_infos_for_cell(
            cell,
            paths,
            require_worktree=True,
            strict=False,
        )
        published: list[dict] = []
        for info in infos:
            sub_wt = info["worktree_path"]
            path = info["path"]
            branch = await self.get_current_branch(sub_wt)
            head = await self.rev_parse(sub_wt, "HEAD") or ""
            old_gitlink = await self._nested_submodule_base_gitlink(cell, path)
            new_gitlink = info.get("gitlink_sha", "")
            zero_gitlink_delta = bool(
                old_gitlink and old_gitlink == new_gitlink == head
            )
            entry = {
                "path": path,
                "worktree_path": sub_wt,
                "repo_root": info.get("module_dir", ""),
                "branch": "" if branch == "HEAD" else branch,
                "base_branch": str(
                    getattr(cell, "worktree_base_branch", "") or ""
                ).strip(),
                "old_gitlink_sha": old_gitlink,
                "new_gitlink_sha": new_gitlink,
                "head_sha": head,
                "zero_gitlink_delta": zero_gitlink_delta,
                "remote": "",
            }

            code, status = await self._git_stdout(
                sub_wt,
                "status",
                "--porcelain",
            )
            if code != 0:
                error = self._nested_preflight_error(
                    entry,
                    "STATUS_FAILED",
                    "Could not read submodule status.",
                )
                error["phase"] = "nested_submodule_publish"
                return error
            if status.strip():
                error = self._nested_preflight_error(
                    entry,
                    "DIRTY",
                    "Commit or checkpoint nested submodule changes first.",
                )
                error["phase"] = "nested_submodule_publish"
                return error

            if zero_gitlink_delta:
                published.append({
                    **entry,
                    "skipped": True,
                    "skip_reason": (
                        "zero_gitlink_delta"
                        if entry["branch"]
                        else "no_gitlink_change_detached_head"
                    ),
                })
                continue

            if head and new_gitlink and head != new_gitlink:
                error = self._nested_preflight_error(
                    entry,
                    "HEAD_MISMATCH",
                    "Submodule HEAD does not match the superproject gitlink.",
                )
                error["phase"] = "nested_submodule_publish"
                return error
            if not head or not new_gitlink:
                return {
                    "ok": False,
                    "phase": "nested_submodule_publish",
                    "condition": "MISSING_HEAD",
                    "error": (
                        f"Nested submodule branch publish failed for {path}: "
                        "could not resolve submodule HEAD/gitlink."
                    ),
                    "submodule": entry,
                }
            if not entry["branch"]:
                if zero_gitlink_delta:
                    published.append({
                        **entry,
                        "skipped": True,
                        "skip_reason": "no_gitlink_change_detached_head",
                    })
                    continue
                return {
                    "ok": False,
                    "phase": "nested_submodule_publish",
                    "condition": "DETACHED_HEAD",
                    "error": (
                        f"Nested submodule branch publish failed for {path}: "
                        "submodule HEAD is detached."
                    ),
                    "submodule": entry,
                }

            remote = await self._nested_submodule_remote_name(sub_wt)
            entry["remote"] = remote
            fetched, fetch_error = await self._nested_submodule_fetch_remote(
                sub_wt,
                remote,
            )
            if not fetched:
                error = self._nested_preflight_error(
                    entry,
                    "REMOTE_UNAVAILABLE",
                    fetch_error,
                )
                error["phase"] = "nested_submodule_publish"
                return error

            pushed = await self._push_nested_submodule_ref(
                sub_wt,
                remote,
                head,
                entry["branch"],
            )
            if not pushed.get("ok"):
                return {
                    "ok": False,
                    "phase": "nested_submodule_publish",
                    "condition": "PUSH_FAILED",
                    "error": (
                        f"Could not push nested submodule {path} branch "
                        f"{entry['branch']} to {remote}: "
                        f"{pushed.get('error', '')}"
                    ).strip(),
                    "submodule": entry,
                }
            published.append({
                **entry,
                "pushed_sha": head,
            })
        return {
            "ok": True,
            "phase": "nested_submodule_publish",
            "submodules": published,
        }

    async def _nested_submodule_pr_entry_base(self, cell, info: dict) -> dict:
        sub_wt = info["worktree_path"]
        path = info["path"]
        branch = await self.get_current_branch(sub_wt)
        head = await self.rev_parse(sub_wt, "HEAD") or ""
        old_gitlink = await self._nested_submodule_base_gitlink(cell, path)
        new_gitlink = info.get("gitlink_sha", "")
        return {
            "path": path,
            "worktree_path": sub_wt,
            "repo_root": info.get("module_dir", ""),
            "branch": "" if branch == "HEAD" else branch,
            "base_branch": str(
                getattr(cell, "worktree_base_branch", "") or ""
            ).strip() or "main",
            "old_gitlink_sha": old_gitlink,
            "new_gitlink_sha": new_gitlink,
            "head_sha": head,
            "zero_gitlink_delta": bool(
                old_gitlink and old_gitlink == new_gitlink == head
            ),
            "remote": "",
            "remote_base_sha": "",
            "remote_refs_containing_gitlink": [],
        }

    async def _nested_submodule_pr_local_gate(self, entry: dict) -> dict:
        sub_wt = entry.get("worktree_path", "")
        code, status = await self._git_stdout(sub_wt, "status", "--porcelain")
        if code != 0:
            error = self._nested_preflight_error(
                entry,
                "STATUS_FAILED",
                "Could not read submodule status.",
            )
            error["phase"] = "nested_submodule_pr_preflight"
            return error
        if status.strip():
            error = self._nested_preflight_error(
                entry,
                "DIRTY",
                "Commit or checkpoint nested submodule changes first.",
            )
            error["phase"] = "nested_submodule_pr_preflight"
            return error

        if entry.get("zero_gitlink_delta"):
            return {"ok": True}

        head = entry.get("head_sha", "")
        new_gitlink = entry.get("new_gitlink_sha", "")
        if head and new_gitlink and head != new_gitlink:
            error = self._nested_preflight_error(
                entry,
                "HEAD_MISMATCH",
                "Submodule HEAD does not match the superproject gitlink.",
            )
            error["phase"] = "nested_submodule_pr_preflight"
            return error
        if not head or not new_gitlink:
            return _worktree_error(
                "nested_submodule_pr_preflight",
                (
                    f"Nested submodule PR flow failed for "
                    f"{entry.get('path', '')}: could not resolve submodule "
                    "HEAD/gitlink."
                ),
                condition="MISSING_HEAD",
                submodule=entry,
            )
        return {"ok": True}

    async def _sync_nested_submodule_main_after_pr(self, entry: dict,
                                                  merged_sha: str) -> dict:
        module_dir = entry.get("repo_root", "")
        sub_wt = entry.get("worktree_path", "")
        remote = entry.get("remote", "") or "origin"
        base = entry.get("base_branch", "") or "main"
        fetched, fetch_error = await self._nested_submodule_fetch_remote(
            sub_wt,
            remote,
        )
        if not fetched:
            return _worktree_error(
                "nested_submodule_pr_sync",
                f"Nested submodule {entry.get('path', '')} remote sync failed: {fetch_error}",
                submodule=entry,
            )
        remote_base_sha = await self._remote_branch_sha(module_dir, remote, base)
        if not remote_base_sha:
            return _worktree_error(
                "nested_submodule_pr_sync",
                f"Could not resolve {remote}/{base} after nested submodule PR merge.",
                submodule=entry,
            )
        if merged_sha and merged_sha != remote_base_sha:
            contains_merged = await self._commit_is_ancestor(
                module_dir,
                merged_sha,
                remote_base_sha,
            )
            if not contains_merged:
                return _worktree_error(
                    "nested_submodule_pr_sync",
                    (
                        f"Nested submodule PR merge commit {merged_sha[:12]} "
                        f"is not reachable from {remote}/{base}."
                    ),
                    submodule=entry,
                    merged_sha=merged_sha,
                    remote_base_sha=remote_base_sha,
                )
        synced = await self._sync_branch_to_remote(
            module_dir,
            remote=remote,
            branch=base,
            force=True,
        )
        if not synced.get("ok"):
            return _worktree_error(
                "nested_submodule_pr_sync",
                (
                    f"Nested submodule {entry.get('path', '')} base sync "
                    f"failed: {synced.get('error', '')}"
                ).strip(),
                submodule=entry,
                base_sync=synced,
            )
        return _worktree_ok(
            "nested_submodule_pr_sync",
            remote=remote,
            base_branch=base,
            remote_base_sha=remote_base_sha,
            base_sync=synced,
        )

    async def _merge_nested_submodule_entry_via_pr(
            self,
            entry: dict,
            *,
            title: str,
            body: str,
            merge: bool,
    ) -> dict:
        path = entry.get("path", "")
        module_dir = entry.get("repo_root", "")
        sub_wt = entry.get("worktree_path", "")
        branch = entry.get("branch", "")
        base = entry.get("base_branch", "") or "main"
        head = entry.get("head_sha", "")
        new_gitlink = entry.get("new_gitlink_sha", "")

        remote = await self._nested_submodule_remote_name(sub_wt)
        entry["remote"] = remote
        fetched, fetch_error = await self._nested_submodule_fetch_remote(
            sub_wt,
            remote,
        )
        if not fetched:
            error = self._nested_preflight_error(
                entry,
                "REMOTE_UNAVAILABLE",
                fetch_error,
            )
            error["phase"] = "nested_submodule_pr_create"
            return error

        remote_base_sha = await self._remote_branch_sha(module_dir, remote, base)
        entry["remote_base_sha"] = remote_base_sha
        contains, refs = await self._remote_contains_commit(
            module_dir,
            remote,
            new_gitlink,
        )
        entry["remote_refs_containing_gitlink"] = refs
        already_on_main = bool(
            remote_base_sha
            and new_gitlink
            and await self._commit_is_ancestor(module_dir, new_gitlink, remote_base_sha)
        )
        if already_on_main:
            return _worktree_ok(
                "nested_submodule_pr_merge",
                **entry,
                skipped=True,
                skip_reason="gitlink_already_on_remote_main",
                reviewed_sha=new_gitlink,
                merged_sha=remote_base_sha,
                merged_main_sha=remote_base_sha,
                already_merged=True,
                pr={},
                needs_gitlink_bump=new_gitlink != remote_base_sha,
            )

        if not branch:
            return _worktree_error(
                "nested_submodule_pr_create",
                (
                    f"Nested submodule PR flow failed for {path}: submodule "
                    "HEAD is detached and the gitlink is not already on "
                    f"{remote}/{base}."
                ),
                condition="DETACHED_HEAD",
                submodule=entry,
            )
        if not contains and head != new_gitlink:
            error = self._nested_preflight_error(
                entry,
                "MISSING_FROM_REMOTE",
                "The gitlink commit is not reachable from the remote and does "
                "not match the nested submodule branch head.",
            )
            error["phase"] = "nested_submodule_pr_create"
            return error

        pushed = await self._push_nested_submodule_pr_head(
            module_dir,
            sub_wt,
            remote,
            head,
            branch,
        )
        if not pushed.get("ok"):
            return _worktree_error(
                pushed.get("phase", "nested_submodule_pr_push"),
                (
                    f"Could not push nested submodule {path} PR branch "
                    f"{branch} to {remote}: {pushed.get('error', '')}"
                ).strip(),
                condition=pushed.get("condition", "PUSH_FAILED"),
                submodule=entry,
                push=pushed,
            )

        pr_result = await self.github_create_or_reuse_pr(
            sub_wt,
            branch,
            base,
            title=title or f"Merge {path} changes",
            body=body or "",
        )
        if not pr_result.get("ok"):
            return _worktree_error(
                "nested_submodule_pr_create",
                pr_result.get("error", "Failed to create nested submodule PR."),
                submodule=entry,
                push=pushed,
                pr=pr_result,
            )
        pr_result["phase"] = "nested_submodule_pr_create"

        if not merge:
            return _worktree_ok(
                "nested_submodule_pr_create",
                **entry,
                pending=True,
                pending_submodule_pr=True,
                reviewed_sha=head,
                pr=pr_result,
                push=pushed,
                message=(
                    "Nested submodule PR is ready; parent PR creation waits "
                    "until the nested PR has merged."
                ),
            )

        already_merged_pr = bool(pr_result.get("already_merged")) or (
            str(pr_result.get("state") or "").upper() == "MERGED"
        )
        if already_merged_pr:
            merge_result = _worktree_ok(
                "nested_submodule_pr_merge",
                url=pr_result.get("url", ""),
                number=pr_result.get("number"),
                head_sha=pr_result.get("head_sha", "") or head,
                merge_commit_sha=pr_result.get("merge_commit_sha", ""),
                pending=False,
                already_merged=True,
                pr_status=pr_result,
            )
        else:
            head_sha = str(pr_result.get("head_sha") or head).strip()
            merge_result = await self.github_request_merge_commit_merge(
                sub_wt,
                pr_result.get("number") or pr_result.get("url", ""),
                head_sha,
                subject=title or f"Merge {path} changes",
                body=body or "",
                url=pr_result.get("url", ""),
                phase="nested_submodule_pr_merge",
            )
            if not merge_result.get("ok") and not merge_result.get("pending"):
                status = merge_result.get("pr_status")
                if isinstance(status, dict) and str(
                    status.get("state") or ""
                ).upper() == "OPEN" and str(
                    status.get("merge_state") or ""
                ).upper() not in {"DIRTY", "UNKNOWN"}:
                    merge_result = await self.github_request_merge_commit_merge(
                        sub_wt,
                        pr_result.get("number") or pr_result.get("url", ""),
                        head_sha,
                        subject=title or f"Merge {path} changes",
                        body=body or "",
                        auto=True,
                        url=pr_result.get("url", ""),
                        phase="nested_submodule_pr_merge",
                    )

        if merge_result.get("pending"):
            return _worktree_ok(
                "nested_submodule_pr_merge",
                **entry,
                pending=True,
                pending_submodule_pr=True,
                reviewed_sha=head,
                pr=pr_result,
                merge=merge_result,
                push=pushed,
            )
        if not merge_result.get("ok"):
            return _worktree_error(
                "nested_submodule_pr_merge",
                merge_result.get(
                    "error",
                    "Nested submodule PR merge failed.",
                ),
                submodule=entry,
                pr=pr_result,
                merge=merge_result,
                push=pushed,
            )

        merged_sha = str(merge_result.get("merge_commit_sha") or "").strip()
        sync = await self._sync_nested_submodule_main_after_pr(entry, merged_sha)
        if not sync.get("ok"):
            return sync
        merged_main_sha = str(sync.get("remote_base_sha") or merged_sha).strip()
        if not merged_main_sha:
            return _worktree_error(
                "nested_submodule_pr_sync",
                "Nested submodule PR merged but remote main SHA is unknown.",
                submodule=entry,
                pr=pr_result,
                merge=merge_result,
            )

        reviewed_is_ancestor = await self._commit_is_ancestor(
            module_dir,
            head,
            merged_main_sha,
        )
        if not reviewed_is_ancestor:
            return _worktree_error(
                "nested_submodule_pr_merge",
                (
                    f"Nested submodule PR for {path} appears to have been "
                    "squash/rebase merged: reviewed head "
                    f"{head[:12]} is not an ancestor of merged main "
                    f"{merged_main_sha[:12]}. ee PRs must use merge commits."
                ),
                condition="UNSUPPORTED_MERGE_STRATEGY",
                submodule=entry,
                pr=pr_result,
                merge=merge_result,
                reviewed_sha=head,
                merged_main_sha=merged_main_sha,
            )

        return _worktree_ok(
            "nested_submodule_pr_merge",
            **entry,
            pending=False,
            reviewed_sha=head,
            merged_sha=merged_sha or merged_main_sha,
            merged_main_sha=merged_main_sha,
            pr=pr_result,
            merge=merge_result,
            push=pushed,
            sync=sync,
            needs_gitlink_bump=True,
        )

    async def merge_nested_submodules_via_pr_for_merge(
            self,
            cell,
            worktree_submodules,
            *,
            title: str = "",
            body: str = "",
            merge: bool = True,
    ) -> dict:
        """Publish configured nested submodule changes through PRs first.

        Zero-gitlink-delta submodules intentionally do not push a branch or
        create a PR.  Real deltas are pushed as PR heads, merge-commit-merged
        into the submodule base branch, then the parent worktree receives a
        mechanical gitlink bump to the merged submodule main SHA.
        """
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return {"ok": True, "phase": "nested_submodule_pr_merge",
                    "submodules": []}
        infos = await self._nested_submodule_infos_for_cell(
            cell,
            paths,
            require_worktree=True,
            strict=False,
        )
        if not infos:
            return {"ok": True, "phase": "nested_submodule_pr_merge",
                    "submodules": []}

        merged: list[dict] = []
        updates: list[dict] = []
        any_pending = False
        any_real_delta = False
        for info in infos:
            entry = await self._nested_submodule_pr_entry_base(cell, info)
            local_gate = await self._nested_submodule_pr_local_gate(entry)
            if not local_gate.get("ok"):
                return local_gate
            if entry.get("zero_gitlink_delta"):
                merged.append({
                    **entry,
                    "skipped": True,
                    "skip_reason": "zero_gitlink_delta",
                })
                continue

            any_real_delta = True
            one = await self._merge_nested_submodule_entry_via_pr(
                entry,
                title=title,
                body=body,
                merge=merge,
            )
            if not one.get("ok"):
                return {
                    **one,
                    "submodules": merged + [one.get("submodule", entry)],
                }
            merged.append(one)
            if one.get("pending"):
                any_pending = True
            merged_sha = str(one.get("merged_main_sha") or one.get("merged_sha") or "")
            if one.get("needs_gitlink_bump") and merged_sha:
                updates.append({"path": entry.get("path", ""), "sha": merged_sha})

        if any_pending:
            return {
                "ok": True,
                "phase": "nested_submodule_pr_merge" if merge else "nested_submodule_pr_create",
                "pending": True,
                "pending_submodule_pr": True,
                "submodules": merged,
                "real_delta": any_real_delta,
            }

        bump = {"ok": True, "committed": False, "paths": []}
        if updates:
            bump = await self._commit_superproject_gitlink_bumps(
                getattr(cell, "worktree_path", "") or "",
                updates,
                message="Update nested submodule gitlinks after PR merge",
            )
            if not bump.get("ok"):
                return _worktree_error(
                    "nested_submodule_gitlink_bump",
                    bump.get(
                        "error",
                        "Nested submodule PR flow could not commit parent gitlink bump.",
                    ),
                    gitlink_bump=bump,
                    submodules=merged,
                )
        return {
            "ok": True,
            "phase": "nested_submodule_pr_merge" if merge else "nested_submodule_pr_create",
            "pending": False,
            "pending_submodule_pr": False,
            "submodules": merged,
            "gitlink_bump": bump,
            "real_delta": any_real_delta,
        }

    async def _commit_superproject_gitlink_bumps(self, wt_dir: str,
                                                 updates: list[dict],
                                                 *,
                                                 message: str) -> dict:
        changed: list[str] = []
        for update in updates:
            path = update.get("path", "")
            sha = update.get("sha", "")
            if not path or not sha:
                continue
            sub_wt = self._join_repo_rel(wt_dir, path)
            code, _out, err = await self._git_run(sub_wt, "reset", "--hard", sha)
            if code != 0:
                return {
                    "ok": False,
                    "error": (
                        f"Could not reset nested submodule {path} to "
                        f"{sha[:12]}: {err}"
                    ),
                }
            code, _out, err = await self._git_run(wt_dir, "add", "--", path)
            if code != 0:
                return {
                    "ok": False,
                    "error": f"Could not stage gitlink for {path}: {err}",
                }
            code, _out = await self._git_stdout(
                wt_dir,
                "diff",
                "--cached",
                "--quiet",
                "--",
                path,
            )
            if code != 0:
                changed.append(path)

        if not changed:
            return {"ok": True, "committed": False, "paths": []}
        code, _out, err = await self._git_run(wt_dir, "commit", "-m", message)
        if code != 0:
            return {
                "ok": False,
                "error": f"Could not commit nested submodule gitlink bump: {err}",
            }
        sha = await self.rev_parse(wt_dir, "HEAD") or ""
        return {"ok": True, "committed": True, "sha": sha, "paths": changed}

    async def _merge_nested_submodules_for_merge(self, cell,
                                                 worktree_submodules,
                                                 *,
                                                 message: str) -> dict:
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return {"ok": True, "submodules": []}
        preflight = await self.nested_submodule_merge_preflight(cell, paths)
        if not preflight.get("ok"):
            return preflight

        base = str(getattr(cell, "worktree_base_branch", "") or "").strip()
        wt_dir = getattr(cell, "worktree_path", "") or ""
        updates: list[dict] = []
        merged: list[dict] = []
        for entry in preflight.get("submodules", []) or []:
            path = entry.get("path", "")
            branch = entry.get("branch", "")
            module_dir = entry.get("repo_root", "")
            remote = entry.get("remote", "") or "origin"
            head = entry.get("head_sha", "")
            old_gitlink = entry.get("old_gitlink_sha", "")
            new_gitlink = entry.get("new_gitlink_sha", "")
            if entry.get("zero_gitlink_delta"):
                merged.append({
                    **entry,
                    "skipped": True,
                    "skip_reason": (
                        "zero_gitlink_delta"
                        if branch
                        else "no_gitlink_change_detached_head"
                    ),
                })
                continue
            if not branch:
                if old_gitlink and old_gitlink == new_gitlink == head:
                    merged.append({
                        **entry,
                        "skipped": True,
                        "skip_reason": "no_gitlink_change_detached_head",
                    })
                    continue
                return {
                    "ok": False,
                    "error": (
                        f"Nested submodule merge failed for {path}: "
                        "submodule HEAD is detached."
                    ),
                    "submodule": entry,
                }

            merge_result: dict = {}
            push_base: dict = {}
            base_sync: dict = {}
            max_attempts = 3
            for attempt in range(max_attempts):
                fetched, fetch_error = await self._nested_submodule_fetch_remote(
                    entry.get("worktree_path", ""),
                    remote,
                )
                if not fetched:
                    return {
                        "ok": False,
                        "error": (
                            f"Nested submodule {path} remote sync failed: "
                            f"{fetch_error}"
                        ),
                        "submodule": entry,
                    }
                base_sync = await self._sync_branch_to_remote(
                    module_dir,
                    remote=remote,
                    branch=base,
                    force=attempt > 0,
                )
                if not base_sync.get("ok"):
                    return {
                        "ok": False,
                        "error": (
                            f"Nested submodule {path} base sync failed: "
                            f"{base_sync.get('error', '')}"
                        ).strip(),
                        "submodule": entry,
                        "base_sync": base_sync,
                    }
                if base_sync.get("remote_sha"):
                    entry["remote_base_sha"] = base_sync.get("remote_sha", "")

                merge_result = await self._merge_branch_into_base_repo(
                    module_dir,
                    base_branch=base,
                    branch=branch,
                    message=(
                        f"Merge nested submodule {path} branch '{branch}'"
                        if not message else
                        f"{message}\n\nNested submodule: {path}\nBranch: {branch}"
                    ),
                )
                if not merge_result.get("ok"):
                    return {
                        "ok": False,
                        "error": merge_result.get(
                            "error",
                            f"Nested submodule merge failed for {path}",
                        ),
                        "submodule": entry,
                    }
                merged_sha = merge_result.get("sha", "")
                push_base = await self._push_nested_submodule_ref(
                    entry.get("worktree_path", ""),
                    remote,
                    merged_sha,
                    base,
                )
                if push_base.get("ok"):
                    break
                push_error = push_base.get("error", "")
                if (
                    attempt >= max_attempts - 1
                    or not self._push_rejected_non_fast_forward(push_error)
                ):
                    break

            if not push_base.get("ok"):
                return {
                    "ok": False,
                    "error": (
                        f"Nested submodule {path} merged locally but could "
                        f"not push {base}: {push_base.get('error', '')}"
                    ),
                    "submodule": entry,
                }
            # Keep the worker submodule branch aligned with the merged base
            # commit so subsequent refresh/remove/rebase checks see a coherent
            # branch pair.  This is a fast-forward after the merge commit above.
            reset_code, _reset_out, reset_err = await self._git_run(
                entry.get("worktree_path", ""),
                "reset",
                "--hard",
                merged_sha,
            )
            if reset_code != 0:
                return {
                    "ok": False,
                    "error": (
                        f"Nested submodule {path} merged locally but could "
                        f"not reset {branch}: {reset_err}"
                    ),
                    "submodule": entry,
                }
            push_branch = await self._push_nested_submodule_ref(
                entry.get("worktree_path", ""),
                remote,
                merged_sha,
                branch,
            )
            if not push_branch.get("ok"):
                return {
                    "ok": False,
                    "error": (
                        f"Nested submodule {path} merged locally but could "
                        f"not push {branch}: {push_branch.get('error', '')}"
                    ),
                    "submodule": entry,
                }
            updates.append({"path": path, "sha": merged_sha})
            merged.append({
                **entry,
                "merged_sha": merged_sha,
                "base_sync": base_sync,
                "merge_changed": bool(merge_result.get("changed")),
            })

        bump = await self._commit_superproject_gitlink_bumps(
            wt_dir,
            updates,
            message="Update nested submodule gitlinks for merge",
        )
        if not bump.get("ok"):
            return bump
        return {
            "ok": True,
            "submodules": merged,
            "gitlink_bump": bump,
        }

    async def _commit_subject(self, repo_root: str, ref: str) -> str:
        code, stdout = await self._git_stdout(
            repo_root, "show", "-s", "--format=%s", ref
        )
        if code != 0:
            return ""
        return stdout.splitlines()[0].strip() if stdout else ""

    async def stale_base_info(self, cell, worktree_submodules=None) -> dict:
        """Return stale-base metadata for a worktree branch.

        A branch is considered stale when its merge-base/fork-point with the
        configured base branch is not the current base branch HEAD. This is a
        cheap, explicit preflight for merge/review paths where trusting a diff
        against an old base can hide or misclassify another stream's changes.
        """
        if not cell or not getattr(cell, "worktree_path", ""):
            return {"stale": False, "error": "No worktree"}
        base = str(getattr(cell, "worktree_base_branch", "") or "").strip()
        branch = str(getattr(cell, "worktree_branch", "") or "").strip()
        if not base or not branch:
            return {"stale": False, "error": "No worktree or base branch"}
        repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path) or ""
        if not repo_root:
            return {"stale": False, "error": "Cannot find repo root"}

        base_head = await self.rev_parse(repo_root, base) or ""
        branch_head = await self.rev_parse(repo_root, branch) or ""
        if not base_head or not branch_head:
            return {
                "stale": False,
                "error": "Cannot resolve worktree branch or base branch",
                "branch": branch,
                "base_branch": base,
            }

        code, fork_point = await self._git_stdout(
            repo_root, "merge-base", base, branch
        )
        if code != 0 or not fork_point:
            return {
                "stale": False,
                "error": "Cannot determine branch fork point",
                "branch": branch,
                "base_branch": base,
                "base_head": base_head,
                "branch_head": branch_head,
            }

        stale = fork_point != base_head
        merged_into_base = False
        if stale:
            try:
                merged_into_base = await self.is_merged(
                    cell,
                    worktree_submodules=worktree_submodules,
                )
            except Exception:
                log.debug(
                    "stale-base merged-state check failed for '%s'",
                    getattr(cell, "name", ""),
                    exc_info=True,
                )
            if merged_into_base:
                stale = False
        info = {
            "stale": stale,
            "branch": branch,
            "base_branch": base,
            "fork_point": fork_point,
            "base_head": base_head,
            "branch_head": branch_head,
            "fork_point_subject": await self._commit_subject(
                repo_root, fork_point
            ),
            "base_head_subject": await self._commit_subject(repo_root, base_head),
            "commits_on_base": 0,
            "files_changed_on_base": 0,
            "agent_hint": (
                str(getattr(cell, "slug", "") or "").strip()
                or str(getattr(cell, "id", "") or "").strip()
                or str(getattr(cell, "name", "") or "").strip()
            ),
        }
        if merged_into_base:
            info["merged"] = True
            info["stale_base_suppressed"] = "branch_changes_already_in_base"
        if stale:
            await self._populate_stale_base_counts(info, repo_root)
            info["warning"] = format_stale_base_warning(info)
            return info

        submodule_paths = _normalize_worktree_submodules(worktree_submodules)
        if not submodule_paths:
            return info
        infos = await self._nested_submodule_infos(
            repo_root,
            cell.worktree_path,
            submodule_paths,
            ref="HEAD",
            require_worktree=True,
            strict=False,
        )
        for sub_info in infos:
            sub_wt = sub_info.get("worktree_path", "")
            module_dir = sub_info.get("module_dir", "")
            sub_branch = await self.get_current_branch(sub_wt)
            if not sub_branch or sub_branch == "HEAD":
                continue
            sub_base_head = await self.rev_parse(module_dir, base) or ""
            sub_branch_head = await self.rev_parse(module_dir, sub_branch) or ""
            if not sub_base_head or not sub_branch_head:
                continue
            code, sub_fork = await self._git_stdout(
                module_dir,
                "merge-base",
                base,
                sub_branch,
            )
            if code != 0 or not sub_fork:
                continue
            if sub_fork == sub_base_head:
                continue
            sub_stale = {
                "stale": True,
                "branch": f"{sub_info.get('path', '')}:{sub_branch}",
                "base_branch": base,
                "fork_point": sub_fork,
                "base_head": sub_base_head,
                "branch_head": sub_branch_head,
                "fork_point_subject": await self._commit_subject(
                    module_dir,
                    sub_fork,
                ),
                "base_head_subject": await self._commit_subject(
                    module_dir,
                    sub_base_head,
                ),
                "commits_on_base": 0,
                "files_changed_on_base": 0,
                "agent_hint": info.get("agent_hint", ""),
                "submodule": sub_info.get("path", ""),
                "submodule_branch": sub_branch,
            }
            await self._populate_stale_base_counts(sub_stale, module_dir)
            sub_stale["warning"] = format_stale_base_warning(sub_stale)
            return sub_stale
        return info

    async def _populate_stale_base_counts(self, info: dict,
                                          repo_root: str) -> None:
        fork_point = str(info.get("fork_point", "") or "").strip()
        base_head = str(info.get("base_head", "") or "").strip()
        if not fork_point or not base_head:
            return
        count_code, count_text = await self._git_stdout(
            repo_root, "rev-list", "--count", f"{fork_point}..{base_head}"
        )
        if count_code == 0 and count_text:
            try:
                info["commits_on_base"] = int(count_text.splitlines()[0])
            except ValueError:
                info["commits_on_base"] = 0
        files_code, files_text = await self._git_stdout(
            repo_root, "diff", "--name-only", fork_point, base_head
        )
        if files_code == 0:
            files = [
                line.strip() for line in files_text.splitlines()
                if line.strip()
            ]
            info["files_changed_on_base"] = len(files)

    async def get_repo_root(self, directory: str) -> Optional[str]:
        """Find Torque's common repo root for a directory.

        For linked worktrees this returns the main/shared repo root rather than
        the linked worktree path so owner and boundary lookups share one key.
        Returns None if *directory* is not inside a git repo.
        """
        directory = os.path.expanduser(directory)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", directory, "rev-parse",
                "--path-format=absolute",
                "--show-toplevel",
                "--git-common-dir",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None
            lines = [line.strip() for line in stdout.decode().splitlines()
                     if line.strip()]
            if not lines:
                return None
            common_root = _repo_root_from_common_dir(
                lines[1] if len(lines) > 1 else ""
            )
            return common_root or lines[0]
        except Exception:
            log.exception("Failed to get repo root for %s", directory)
            return None

    async def get_current_branch(self, repo_root: str) -> str:
        """Get the current branch name (or HEAD if detached)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root, "rev-parse", "--abbrev-ref", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return stdout.decode().strip()
        except Exception:
            log.debug("Could not get current branch for %s", repo_root)
        return "HEAD"

    async def _resolve_safe_default_base(self, repo_root: str) -> str:
        """Resolve the repo's default base branch for the A1 safety guard.

        Used only when refusing a worker-namespaced HEAD as a worktree base.
        Prefers an existing ``main``/``master`` (the codebase's default-branch
        convention) and falls back to ``main``.
        """
        for candidate in ("main", "master"):
            if await self._branch_exists(repo_root, candidate):
                return candidate
        return "main"

    async def reconcile_worktree_branch(self, cell) -> bool:
        """Sync ``cell.worktree_branch`` with the worktree's live HEAD branch.

        A worker reused across two tasks can end up checked out on a new
        branch (created for the re-dispatched task) while ``worktree_branch``
        still names the original task's branch. The merge/rebase/diff and
        stale-base machinery resolve the agent through ``worktree_branch``, so
        a stale mapping silently targets the wrong branch (see the reused-worker
        merge bug). Re-read the actual checked-out branch and update the cached
        field when they diverge.

        Returns True when ``cell.worktree_branch`` was changed (callers should
        persist/emit), False otherwise. Detached HEAD and lookup failures are
        left untouched so a real branch name is never clobbered.
        """
        if not cell or not getattr(cell, "worktree_path", ""):
            return False
        if not await self.validate(cell):
            return False
        actual = await self.get_current_branch(cell.worktree_path)
        actual = str(actual or "").strip()
        if not actual or actual == "HEAD":
            return False
        if actual == (str(getattr(cell, "worktree_branch", "") or "").strip()):
            return False
        log.info(
            "Reconciled worktree branch for '%s': %s -> %s",
            getattr(cell, "name", "") or getattr(cell, "id", ""),
            getattr(cell, "worktree_branch", "") or "(unset)",
            actual,
        )
        cell.worktree_branch = actual
        return True

    async def list_worktrees(self, repo_root: str) -> list[dict]:
        """List git worktrees for a repository."""
        if not repo_root:
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "worktree", "list", "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []
            return _parse_worktree_list_porcelain(stdout.decode())
        except Exception:
            log.debug("Could not list worktrees for %s", repo_root)
            return []

    async def _diff_name_only(self, directory: str, *refs: str) -> list[str]:
        if not directory or not refs:
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", directory,
                "diff", "--name-only", "-z", *refs,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []
            return [
                _normalize_repo_rel_path(path)
                for path in stdout.decode("utf-8", errors="replace").split("\0")
                if _normalize_repo_rel_path(path)
            ]
        except Exception:
            log.debug("Could not diff changed paths in %s", directory)
            return []

    async def untracked_files(self, directory: str) -> list[str]:
        """List untracked, non-ignored files for a worktree/repo."""
        if not directory:
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", directory,
                "ls-files", "--others", "--exclude-standard", "-z",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []
            return [
                _normalize_repo_rel_path(path)
                for path in stdout.decode("utf-8", errors="replace").split("\0")
                if _normalize_repo_rel_path(path)
            ]
        except Exception:
            log.debug("Could not list untracked files in %s", directory)
            return []

    async def untracked_overwrite_paths(self, directory: str,
                                        target_paths: list[str]) -> list[str]:
        """Return untracked paths that would be overwritten by target paths."""
        untracked = await self.untracked_files(directory)
        return _find_untracked_overwrite_paths(untracked, target_paths)

    async def merge_untracked_overwrite_paths(self, repo_root: str,
                                              base_branch: str,
                                              tree_sha: str) -> list[str]:
        """Return base-repo untracked files a merge would overwrite."""
        if not repo_root or not base_branch or not tree_sha:
            return []
        checked_out = await self.get_current_branch(repo_root)
        if checked_out != base_branch:
            return []
        base_sha = await self.rev_parse(repo_root, base_branch)
        if not base_sha or base_sha == tree_sha:
            return []
        target_paths = await self._diff_name_only(repo_root, base_sha, tree_sha)
        return await self.untracked_overwrite_paths(repo_root, target_paths)

    async def rebase_untracked_overwrite_paths(self, cell) -> list[str]:
        """Return worktree untracked files a rebase would overwrite."""
        if not cell.worktree_path or not cell.worktree_base_branch:
            return []
        target_paths = await self._diff_name_only(
            cell.worktree_path,
            "HEAD",
            cell.worktree_base_branch,
        )
        return await self.untracked_overwrite_paths(
            cell.worktree_path,
            target_paths,
        )

    async def _branch_exists(self, repo_root: str, branch: str) -> bool:
        """Return whether a local branch already exists."""
        if not branch:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root, "show-ref", "--verify", "--quiet",
                f"refs/heads/{branch}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            log.debug("Could not check for existing branch %s", branch)
            return False

    async def _resolve_worktree_target(self, cell, repo_root: str, base_dir: str,
                                       worktree_name: str = "",
                                       state=None) -> tuple[str, str]:
        """Choose the final worktree branch/path pair for this creation."""
        branch_prefix = _branch_prefix_for_agent(cell, state)
        requested = _slugify_worktree_name(worktree_name)
        if not requested:
            slug = _agent_branch_slug(cell)
            short_id = cell.id[:7]
            branch = f"{branch_prefix}{slug}-{short_id}"
            wt_path = os.path.join(
                _resolve_worktree_base_path(repo_root, base_dir),
                cell.id,
            )
            return branch, wt_path

        base_path = _resolve_worktree_base_path(repo_root, base_dir)
        candidate = requested
        suffix_index = 2
        while True:
            branch_leaf = _custom_branch_leaf_for_agent(cell, candidate)
            branch = f"{branch_prefix}{branch_leaf}"
            wt_path = os.path.realpath(os.path.join(base_path, candidate))
            if os.path.commonpath([base_path, wt_path]) != base_path:
                candidate = _dedupe_worktree_name(requested, suffix_index)
                suffix_index += 1
                continue
            if not await self._branch_exists(repo_root, branch) \
                    and not os.path.lexists(wt_path):
                return branch, wt_path
            candidate = _dedupe_worktree_name(requested, suffix_index)
            suffix_index += 1

    async def create(self, cell, repo_root: str,
                     base_dir: str = ".torque/worktrees",
                     base_branch: str = "",
                     symlinks: list[str] | None = None,
                     include_gitignored_symlinks: bool = False,
                     worktree_name: str = "",
                     worktree_submodules=None,
                     state=None) -> Optional[str]:
        """Create a git worktree for the cell.

        Args:
            cell: AgentCell to create the worktree for.
            repo_root: Absolute path to the git repo root.
            base_dir: Directory name for worktrees (relative to repo root).
            base_branch: Branch to fork from (empty = current HEAD).
            symlinks: Relative paths or glob patterns to symlink from repo
                root into worktree.
            include_gitignored_symlinks: When true, use git ignore rules to
                symlink ignored files/directories from the repo root into the
                worktree. This is opt-in and intentionally skips Torque's
                runtime directory.
            worktree_name: Optional custom name for the worktree folder and
                branch suffix.
            worktree_submodules: Configured submodule paths that should become
                linked nested worktrees inside the superproject worktree.
            state: MatrixState-like object used to resolve owner engineer
                slugs for worker branch namespacing.

        Returns:
            Absolute path to the worktree, or None on failure.
        """
        try:
            branch, wt_path = await self._resolve_worktree_target(
                cell,
                repo_root,
                base_dir,
                worktree_name=worktree_name,
                state=state,
            )
            os.makedirs(os.path.dirname(wt_path), exist_ok=True)

            # Ensure .torque directory exists
            torque_dir = os.path.join(repo_root, ".torque")
            os.makedirs(torque_dir, exist_ok=True)

            # A1 base-branch safety guard (TORQUE:604). With no explicit base,
            # the worktree forks from the repo-root HEAD. If that HEAD has been
            # left on a worker-namespaced branch (orphan/race/manual checkout),
            # forking off it would propagate another worker's in-flight commits
            # into this fresh worktree (the basing-off complement to the :580
            # commit-into-shared-main block). Refuse it and fork off the
            # configured default branch instead. The healthy path — explicit
            # base, or HEAD on main / a normal branch — is left untouched.
            head_branch = ""
            if not base_branch:
                head_branch = (
                    await self.get_current_branch(repo_root) or ""
                ).strip()
                if is_worker_namespaced_branch(head_branch):
                    safe_base = await self._resolve_safe_default_base(repo_root)
                    log.warning(
                        "refusing worker-branch base '%s' for '%s'; "
                        "basing off '%s'",
                        head_branch, cell.name, safe_base,
                    )
                    base_branch = safe_base

            # Build the git worktree add command
            cmd = ["git", "-C", repo_root, "worktree", "add",
                   "-b", branch, wt_path]
            if base_branch:
                cmd.append(base_branch)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                log.error("git worktree add failed for '%s': %s",
                          cell.name, stderr.decode().strip())
                return None

            # Record the base branch for future reference. ``head_branch`` was
            # already resolved above when no explicit base was given.
            if not base_branch:
                base_branch = head_branch or await self.get_current_branch(
                    repo_root)

            submodule_paths = _normalize_worktree_submodules(worktree_submodules)
            if submodule_paths:
                try:
                    await self._create_nested_submodule_worktrees(
                        repo_root,
                        wt_path,
                        branch,
                        submodule_paths,
                    )
                except Exception as exc:
                    log.error(
                        "Nested submodule worktree create failed for '%s': %s",
                        cell.name,
                        exc,
                    )
                    await self.remove_path_result(
                        repo_root,
                        wt_path,
                        branch=branch,
                        name=cell.name,
                        force=True,
                    )
                    return None

            cell.worktree_path = wt_path
            cell.worktree_branch = branch
            cell.worktree_repo_root = repo_root
            cell.worktree_base_branch = base_branch
            try:
                from .worktree_streams import invalidate_branch_exists_cache
                invalidate_branch_exists_cache(repo_root, branch)
            except Exception:
                log.debug("Failed to invalidate branch cache", exc_info=True)
            log.info("Created worktree for '%s': %s (branch %s, base %s)",
                     cell.name, wt_path, branch, base_branch)

            # Add .torque/ to .gitignore if not already there
            await self._ensure_gitignore(repo_root)

            # Keep Claude Code's opportunistic auto-memory out of isolated
            # Torque agent worktrees; the file is covered by git exclude.
            _configure_claude_code_worktree_settings(cell, wt_path)

            # Create configured symlinks. Explicit paths are applied first;
            # optional gitignored paths are additive and deduped below.
            symlink_paths: list[str] = []
            if symlinks:
                symlink_paths.extend(
                    self._expand_symlink_paths(repo_root, symlinks)
                )
            if include_gitignored_symlinks:
                symlink_paths.extend(
                    await self._expand_gitignored_symlink_paths(
                        repo_root,
                        base_dir,
                    )
                )
            if symlink_paths:
                self._create_symlink_paths(wt_path, repo_root, symlink_paths)

            return wt_path
        except Exception:
            log.exception("Failed to create worktree for '%s'", cell.name)
            return None

    def _normalize_symlink_pattern(self, raw_path: str) -> str:
        """Return a normalized repo-relative symlink path/pattern."""
        raw_path = str(raw_path or "").strip()
        if not raw_path:
            return ""
        if os.path.isabs(raw_path):
            log.warning("Skipping absolute symlink path: %s", raw_path)
            return ""

        trimmed = raw_path.strip("/")
        if not trimmed:
            return ""

        parts = [part for part in trimmed.split("/") if part]
        if any(part == ".." for part in parts):
            log.warning("Skipping invalid symlink path: %s", raw_path)
            return ""

        normalized = os.path.normpath(trimmed).replace(os.sep, "/")
        if normalized in {"", "."}:
            return ""
        return normalized

    def _expand_symlink_paths(self, repo_root: str,
                              symlinks: list[str]) -> list[str]:
        """Expand configured symlink paths/patterns within ``repo_root``."""
        expanded: list[str] = []
        seen: set[str] = set()

        for raw_path in symlinks:
            pattern = self._normalize_symlink_pattern(raw_path)
            if not pattern:
                continue

            if not glob.has_magic(pattern):
                matches = [pattern]
            else:
                full_pattern = os.path.join(repo_root, pattern)
                resolved_matches = []
                for match in glob.glob(full_pattern, recursive=True):
                    rel_path = os.path.relpath(match, repo_root)
                    if rel_path in {".", ""}:
                        continue
                    rel_path = os.path.normpath(rel_path).replace(
                        os.sep, "/")
                    if rel_path == ".." or rel_path.startswith("../"):
                        log.warning(
                            "Skipping symlink match outside repo root: %s",
                            match,
                        )
                        continue
                    resolved_matches.append(rel_path)

                matches = sorted(set(resolved_matches))
                if not matches:
                    log.warning(
                        "Symlink pattern did not match any paths, skipping: %s",
                        pattern,
                    )

            for rel_path in matches:
                if rel_path in seen:
                    continue
                seen.add(rel_path)
                expanded.append(rel_path)

        return expanded

    @staticmethod
    def _split_git_nul_output(data: bytes) -> list[str]:
        """Return NUL-delimited git path output as text paths."""
        if not data:
            return []
        text = data.decode("utf-8", "surrogateescape")
        return [item for item in text.split("\0") if item]

    async def _git_ls_ignored_candidates(self, repo_root: str) -> list[str]:
        """Ask git for ignored/untracked path candidates."""
        cmd = [
            "git",
            "-C",
            repo_root,
            "ls-files",
            "-z",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.warning(
                "Failed to enumerate gitignored symlink candidates in %s: %s",
                repo_root,
                stderr.decode("utf-8", "replace").strip(),
            )
            return []
        return self._split_git_nul_output(stdout)

    async def _git_check_ignored_paths(self, repo_root: str,
                                       paths: list[str]) -> list[str]:
        """Filter candidate paths through git's ignore engine."""
        if not paths:
            return []
        payload = ("\0".join(paths) + "\0").encode(
            "utf-8",
            "surrogateescape",
        )
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            repo_root,
            "check-ignore",
            "-z",
            "--stdin",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(payload)
        if proc.returncode == 1:
            return []
        if proc.returncode != 0:
            log.warning(
                "Failed to filter gitignored symlink candidates in %s: %s",
                repo_root,
                stderr.decode("utf-8", "replace").strip(),
            )
            return []
        return self._split_git_nul_output(stdout)

    def _repo_relative_worktree_base_dir(self, repo_root: str,
                                         base_dir: str) -> str:
        """Return configured worktree base dir relative to repo, if inside."""
        repo_abs = os.path.realpath(os.path.expanduser(repo_root))
        base_abs = _resolve_worktree_base_path(repo_abs, base_dir)
        try:
            if os.path.commonpath([repo_abs, base_abs]) != repo_abs:
                return ""
        except ValueError:
            return ""
        rel_path = os.path.relpath(base_abs, repo_abs)
        return _normalize_repo_rel_path(rel_path)

    @staticmethod
    def _is_dot_git_path(rel_path: str) -> bool:
        return rel_path == ".git" or rel_path.startswith(".git/")

    @staticmethod
    def _is_torque_runtime_path(rel_path: str) -> bool:
        return rel_path == ".torque" or rel_path.startswith(".torque/")

    @staticmethod
    def _is_same_ancestor_or_descendant(path: str, other: str) -> bool:
        if not path or not other:
            return False
        return (
            path == other
            or path.startswith(other + "/")
            or other.startswith(path + "/")
        )

    def _normalize_gitignored_symlink_candidate(self, raw_path: str) -> str:
        """Normalize a git-reported ignored path without parsing ignores."""
        raw_path = str(raw_path or "")
        if not raw_path:
            return ""
        if os.path.isabs(raw_path):
            log.warning("Skipping absolute gitignored symlink path: %s",
                        raw_path)
            return ""
        trimmed = raw_path.strip("/")
        if not trimmed:
            return ""
        parts = [part for part in trimmed.split("/") if part]
        if any(part == ".." for part in parts):
            log.warning("Skipping invalid gitignored symlink path: %s",
                        raw_path)
            return ""
        normalized = os.path.normpath(trimmed).replace(os.sep, "/")
        if normalized in {"", "."}:
            return ""
        return normalized

    def _filter_gitignored_symlink_candidates(
            self, repo_root: str, base_dir: str,
            paths: list[str]) -> list[str]:
        """Apply Torque safety rules to gitignored symlink candidates."""
        repo_abs = os.path.abspath(os.path.expanduser(repo_root))
        base_rel = self._repo_relative_worktree_base_dir(repo_abs, base_dir)
        filtered: list[str] = []
        seen: set[str] = set()

        for raw_path in paths:
            rel_path = self._normalize_gitignored_symlink_candidate(raw_path)
            if not rel_path or rel_path in seen:
                continue
            if self._is_dot_git_path(rel_path):
                log.debug("Skipping .git symlink candidate: %s", rel_path)
                continue
            if self._is_torque_runtime_path(rel_path):
                log.debug(
                    "Skipping Torque runtime symlink candidate: %s",
                    rel_path,
                )
                continue
            if self._is_same_ancestor_or_descendant(rel_path, base_rel):
                log.debug(
                    "Skipping worktree base symlink candidate: %s",
                    rel_path,
                )
                continue
            target = os.path.abspath(os.path.join(repo_abs, rel_path))
            try:
                if os.path.commonpath([repo_abs, target]) != repo_abs:
                    log.warning(
                        "Skipping gitignored symlink target outside repo: %s",
                        target,
                    )
                    continue
            except ValueError:
                log.warning(
                    "Skipping invalid gitignored symlink target: %s",
                    target,
                )
                continue
            seen.add(rel_path)
            filtered.append(rel_path)

        return filtered

    async def _expand_gitignored_symlink_paths(
            self, repo_root: str, base_dir: str) -> list[str]:
        """Return ignored paths to symlink, resolved by git ignore rules.

        Discovery is best-effort and fails open: the subprocess helpers
        already return ``[]`` on nonzero git exit, and any spawn/IO failure
        (e.g. git binary missing, OSError on Popen) is swallowed here so a
        broken discovery never aborts worktree creation.
        """
        try:
            candidates = await self._git_ls_ignored_candidates(repo_root)
            if not candidates:
                return []
            ignored_paths = await self._git_check_ignored_paths(
                repo_root,
                candidates,
            )
            return self._filter_gitignored_symlink_candidates(
                repo_root,
                base_dir,
                ignored_paths,
            )
        except Exception:
            log.exception(
                "Failed to discover gitignored symlink paths in %s; "
                "continuing without auto-symlinks",
                repo_root,
            )
            return []

    def _create_symlinks(self, wt_path: str, repo_root: str,
                         symlinks: list[str]) -> None:
        """Create symlinks in worktree pointing to repo root paths."""
        self._create_symlink_paths(
            wt_path,
            repo_root,
            self._expand_symlink_paths(repo_root, symlinks),
        )

    def _create_symlink_paths(self, wt_path: str, repo_root: str,
                              rel_paths: list[str]) -> None:
        """Create symlinks in worktree for normalized repo-relative paths."""
        created = []
        seen: set[str] = set()
        for rel_path in rel_paths:
            rel_path = self._normalize_symlink_pattern(rel_path)
            if not rel_path or rel_path in seen:
                continue
            seen.add(rel_path)
            if self._is_dot_git_path(rel_path):
                log.warning("Skipping .git symlink path: %s", rel_path)
                continue
            target = os.path.join(repo_root, rel_path)
            link = os.path.join(wt_path, rel_path)
            if not os.path.exists(target):
                log.warning("Symlink target does not exist, skipping: %s",
                            target)
                continue
            if os.path.lexists(link):
                log.debug("Path already exists in worktree, skipping "
                          "symlink: %s", link)
                continue
            parent = os.path.dirname(link)
            if os.path.lexists(parent) and not os.path.isdir(parent):
                log.debug(
                    "Symlink parent exists and is not a directory, "
                    "skipping: %s",
                    parent,
                )
                continue
            try:
                os.makedirs(parent, exist_ok=True)
                os.symlink(target, link)
                log.info("Created symlink %s → %s", link, target)
                created.append(rel_path)
            except OSError:
                log.exception("Failed to create symlink %s → %s",
                              link, target)
        if created:
            self._add_to_worktree_exclude(wt_path, created)

    def _add_to_worktree_exclude(self, wt_path: str,
                                 paths: list[str]) -> None:
        """Add paths to the worktree-local git exclude file."""
        dot_git = os.path.join(wt_path, ".git")
        try:
            with open(dot_git) as f:
                gitdir = f.read().strip().removeprefix("gitdir: ")
            exclude = os.path.join(gitdir, "info", "exclude")
            os.makedirs(os.path.dirname(exclude), exist_ok=True)
            with open(exclude, "a") as f:
                for p in paths:
                    f.write(f"{p}\n")
            log.debug("Added %d paths to worktree exclude: %s", len(paths),
                      exclude)
        except Exception:
            log.exception("Failed to update worktree exclude file")

    async def validate_existing_worktree(
            self,
            worktree_path: str,
            *,
            repo_root: str = "",
            branch: str = "",
            base_branch: str = "",
            worktree_submodules=None,
    ) -> ExistingWorktreeTarget:
        """Validate an already-existing linked worktree without mutating refs.

        This is the shared path/branch target primitive for driverless
        merge/PR/remove/adopt flows. It intentionally performs only reads:
        no checkout, branch creation, reset, or worktree creation.
        """
        wt_path = os.path.realpath(os.path.expanduser(
            str(worktree_path or "").strip()
        ))
        if not wt_path or not os.path.isdir(wt_path):
            raise ValueError("worktree_path must be an existing directory")

        resolved_repo = str(repo_root or "").strip()
        if resolved_repo:
            resolved_repo = os.path.realpath(os.path.expanduser(resolved_repo))
        else:
            resolved_repo = await self.get_repo_root(wt_path) or ""
            if resolved_repo:
                resolved_repo = os.path.realpath(os.path.expanduser(resolved_repo))
        if not resolved_repo or not os.path.isdir(resolved_repo):
            raise ValueError("repo_root could not be resolved for worktree_path")

        entries = await self.list_worktrees(resolved_repo)
        entry = None
        for candidate in entries:
            cand_path = str(candidate.get("path", "") or "").strip()
            if cand_path and self._same_worktree_path(cand_path, wt_path):
                entry = dict(candidate)
                break
        if not entry:
            raise ValueError("worktree_path is not listed by git worktree list")
        if entry.get("bare"):
            raise ValueError("bare worktrees cannot be targeted")
        if entry.get("detached"):
            raise ValueError("worktree is detached; a branch target is required")

        actual_branch = (await self.get_current_branch(wt_path) or "").strip()
        if not actual_branch or actual_branch == "HEAD":
            raise ValueError("worktree is detached; a branch target is required")
        requested_branch = str(branch or "").strip() or actual_branch
        if requested_branch != actual_branch:
            raise ValueError(
                f"requested branch {requested_branch!r} does not match "
                f"worktree branch {actual_branch!r}"
            )
        entry_branch = str(entry.get("branch", "") or "").strip()
        if entry_branch and entry_branch != requested_branch:
            raise ValueError(
                f"git worktree list reports branch {entry_branch!r}, not "
                f"{requested_branch!r}"
            )
        if not await self._branch_exists(resolved_repo, requested_branch):
            raise ValueError(f"local branch does not exist: {requested_branch}")

        head_sha = await self.rev_parse(wt_path, "HEAD") or ""
        branch_sha = await self.rev_parse(resolved_repo, requested_branch) or ""
        if not head_sha:
            raise ValueError("could not resolve worktree HEAD")
        if not branch_sha:
            raise ValueError(f"could not resolve branch: {requested_branch}")
        if head_sha != branch_sha:
            raise ValueError(
                "worktree HEAD does not match requested branch tip "
                f"({head_sha[:12]} != {branch_sha[:12]})"
            )

        probe = type("ExistingWorktreeProbe", (), {
            "id": f"driverless:{requested_branch}",
            "name": requested_branch,
            "worktree_path": wt_path,
            "worktree_repo_root": resolved_repo,
            "git_root": resolved_repo,
            "worktree_branch": requested_branch,
            "worktree_base_branch": str(base_branch or "").strip(),
        })()
        is_dirty = await self.has_uncommitted_changes(
            probe,
            worktree_submodules=worktree_submodules,
        )
        return ExistingWorktreeTarget(
            repo_root=resolved_repo,
            worktree_path=wt_path,
            branch=requested_branch,
            head_sha=head_sha,
            base_branch=str(base_branch or "").strip(),
            git_root=resolved_repo,
            is_dirty=bool(is_dirty),
            listed_worktree_entry=entry,
        )

    async def verify_mechanical_gitlink_commit(
            self,
            cell,
            *,
            previous_head: str,
            new_head: str,
            worktree_submodules,
    ) -> dict:
        """Machine-verify a boundary advance as exactly one gitlink-only commit."""
        repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
        wt_path = str(getattr(cell, "worktree_path", "") or "").strip()
        previous_head = str(previous_head or "").strip()
        new_head = str(new_head or "").strip()
        allowed_paths = _normalize_worktree_submodules(worktree_submodules)
        if not repo_root or not wt_path:
            return {"ok": False, "reason": "missing_worktree"}
        if not previous_head or not new_head:
            return {"ok": False, "reason": "missing_head"}
        if not allowed_paths:
            return {"ok": False, "reason": "no_configured_submodules"}

        current_head = await self.rev_parse(wt_path, "HEAD") or ""
        if current_head != new_head:
            return {
                "ok": False,
                "reason": "new_head_mismatch",
                "current_head": current_head,
                "new_head": new_head,
            }
        code, _out, _err = await self._git_run(
            repo_root,
            "merge-base",
            "--is-ancestor",
            previous_head,
            new_head,
        )
        if code != 0:
            return {"ok": False, "reason": "previous_not_ancestor"}
        code, count_out, err = await self._git_run(
            repo_root,
            "rev-list",
            "--count",
            f"{previous_head}..{new_head}",
        )
        if code != 0:
            return {"ok": False, "reason": "rev_list_failed", "error": err}
        try:
            commit_count = int((count_out or "0").strip())
        except ValueError:
            commit_count = -1
        if commit_count != 1:
            return {
                "ok": False,
                "reason": "expected_exactly_one_commit",
                "commit_count": commit_count,
            }
        code, commit_out, err = await self._git_run(
            repo_root,
            "rev-list",
            "--max-count=1",
            f"{previous_head}..{new_head}",
        )
        if code != 0 or not commit_out.strip():
            return {"ok": False, "reason": "mechanical_commit_missing", "error": err}
        mechanical_commit = commit_out.strip().splitlines()[0]

        code, raw_out, err = await self._git_run(
            repo_root,
            "diff",
            "--raw",
            "--no-renames",
            previous_head,
            new_head,
        )
        if code != 0:
            return {"ok": False, "reason": "diff_failed", "error": err}
        changed_paths: list[str] = []
        raw_entries: list[dict] = []
        for line in (raw_out or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                meta, path = line.split("\t", 1)
            except ValueError:
                return {"ok": False, "reason": "diff_parse_failed", "raw": raw_out}
            parts = meta.split()
            if len(parts) < 5:
                return {"ok": False, "reason": "diff_parse_failed", "raw": raw_out}
            old_mode = parts[0].lstrip(":")
            new_mode = parts[1]
            status = parts[4]
            norm_path = _normalize_repo_rel_path(path)
            raw_entries.append({
                "path": norm_path,
                "old_mode": old_mode,
                "new_mode": new_mode,
                "status": status,
            })
            if old_mode != "160000" or new_mode != "160000":
                return {
                    "ok": False,
                    "reason": "non_gitlink_diff",
                    "path": norm_path,
                    "old_mode": old_mode,
                    "new_mode": new_mode,
                }
            if norm_path not in allowed_paths:
                return {
                    "ok": False,
                    "reason": "path_not_configured_submodule",
                    "path": norm_path,
                    "allowed_paths": allowed_paths,
                }
            changed_paths.append(norm_path)
        if not changed_paths:
            return {"ok": False, "reason": "empty_diff"}

        dirty = await self.has_uncommitted_changes(
            cell,
            worktree_submodules=allowed_paths,
        )
        if dirty:
            return {"ok": False, "reason": "dirty_worktree"}

        submodule_states = await self.nested_submodule_head_states(
            cell,
            allowed_paths,
        )
        sub_by_path = {item.get("path", ""): item for item in submodule_states}
        for path in allowed_paths:
            state = sub_by_path.get(path)
            if not state:
                return {"ok": False, "reason": "missing_submodule_state", "path": path}
            if state.get("commit_sha", "") != state.get("gitlink_sha", ""):
                return {
                    "ok": False,
                    "reason": "submodule_head_gitlink_mismatch",
                    "path": path,
                    "commit_sha": state.get("commit_sha", ""),
                    "gitlink_sha": state.get("gitlink_sha", ""),
                }

        return {
            "ok": True,
            "mechanical_commit": mechanical_commit,
            "paths": sorted(set(changed_paths)),
            "raw_entries": raw_entries,
            "submodules": submodule_states,
        }

    async def _safe_deinit_submodules_for_remove(
            self,
            worktree_path: str,
            submodule_paths: list[str],
    ) -> dict:
        result = {"ok": True, "deinitialized": [], "errors": []}
        for path in submodule_paths:
            code, out, err = await self._git_run(
                worktree_path,
                "submodule",
                "deinit",
                "--",
                path,
            )
            entry = {
                "path": path,
                "returncode": code,
                "stdout": out,
                "stderr": err,
            }
            if code != 0:
                result["ok"] = False
                result["errors"].append(entry)
            else:
                result["deinitialized"].append(entry)
        return result

    async def safe_remove_existing_worktree(
            self,
            target: ExistingWorktreeTarget | dict,
            *,
            delete_branch: bool = True,
            worktree_submodules=None,
    ) -> dict:
        """Safely remove a validated existing worktree without force defaults."""
        if isinstance(target, ExistingWorktreeTarget):
            target_dict = target.to_dict()
        else:
            target_dict = dict(target or {})
        repo_root = str(target_dict.get("repo_root", "") or "").strip()
        worktree_path = str(target_dict.get("worktree_path", "") or "").strip()
        branch = str(target_dict.get("branch", "") or "").strip()
        base_branch = str(target_dict.get("base_branch", "") or "").strip()
        result = {
            "ok": False,
            "worktree_removed": False,
            "branch_deleted": not bool(delete_branch and branch),
            "branch_preserved": bool(not delete_branch and branch),
            "path": worktree_path,
            "branch": branch,
            "base_branch": base_branch,
            "delete_branch": bool(delete_branch),
            "safe": True,
            "forced": False,
            "deinit": {},
            "nested_submodules": [],
            "pruned": [],
            "mismatches": [],
            "message": "",
        }
        try:
            validated = await self.validate_existing_worktree(
                worktree_path,
                repo_root=repo_root,
                branch=branch,
                base_branch=base_branch,
                worktree_submodules=worktree_submodules,
            )
        except ValueError as exc:
            result["message"] = str(exc)
            result["mismatches"].append("target_validation_failed")
            return result
        if validated.is_dirty:
            result["message"] = "Refusing to remove dirty worktree"
            result["mismatches"].append("dirty_worktree")
            return result
        branch = validated.branch
        repo_root = validated.repo_root
        worktree_path = validated.worktree_path
        base_branch = validated.base_branch or base_branch
        submodule_paths = _normalize_worktree_submodules(worktree_submodules)
        probe = type("SafeRemoveProbe", (), {
            "id": f"driverless:{branch}",
            "name": branch,
            "worktree_path": worktree_path,
            "worktree_repo_root": repo_root,
            "worktree_branch": branch,
            "worktree_base_branch": base_branch,
        })()
        if delete_branch:
            if not base_branch:
                result["message"] = "base_branch is required to delete branch safely"
                result["mismatches"].append("missing_base_branch")
                return result
            if submodule_paths:
                merged = bool(
                    await self.is_branch_merged(
                        repo_root,
                        branch=branch,
                        base_branch=base_branch,
                    )
                    and await self._nested_submodule_branches_merged(
                        probe,
                        repo_root,
                        submodule_paths,
                    )
                )
            else:
                merged = await self.is_branch_merged(
                    repo_root,
                    branch=branch,
                    base_branch=base_branch,
                )
            if not merged:
                result["message"] = (
                    f"Refusing to delete unmerged branch {branch!r} "
                    f"relative to {base_branch!r}"
                )
                result["mismatches"].append("branch_not_merged")
                return result

        infos = []
        if submodule_paths:
            infos = await self._nested_submodule_infos(
                repo_root,
                worktree_path,
                submodule_paths,
                ref="HEAD",
                require_worktree=False,
                strict=False,
            )

        remove_result = await self.remove_path_result(
            repo_root,
            worktree_path,
            branch=branch if delete_branch else "",
            name=branch or worktree_path,
            force=False,
            worktree_submodules=submodule_paths,
        )
        result.update({
            key: value
            for key, value in remove_result.items()
            if key not in {"ok", "message"}
        })
        result["branch"] = branch
        result["base_branch"] = base_branch
        result["nested_submodules"] = remove_result.get("nested_submodules", [])
        result["mismatches"] = list(remove_result.get("mismatches", []) or [])
        result["worktree_removed"] = bool(remove_result.get("worktree_removed"))
        result["branch_deleted"] = (
            bool(remove_result.get("branch_deleted")) if delete_branch
            else False
        )
        result["branch_preserved"] = bool(not delete_branch and branch)
        await self.prune_admin(repo_root)
        result["pruned"].append({"repo_root": repo_root})
        for info in infos:
            module_dir = str(info.get("module_dir", "") or "").strip()
            if module_dir:
                await self.prune_admin(module_dir)
                result["pruned"].append({"repo_root": module_dir})
                if info.get("repo_root"):
                    result.setdefault("module_core_worktree", []).append(
                        await self._ensure_submodule_module_core_worktree_for_info(info)
                    )
        result["ok"] = bool(
            result["worktree_removed"]
            and (
                (delete_branch and result["branch_deleted"])
                or (not delete_branch)
            )
        )
        if result["ok"]:
            result["message"] = (
                "Worktree removed; branch preserved"
                if not delete_branch else "Worktree removed"
            )
        else:
            result["message"] = remove_result.get(
                "message",
                "Safe worktree removal failed",
            )
        return result

    async def remove_path(self, repo_root: str, worktree_path: str, *,
                          branch: str = "",
                          name: str = "",
                          force: bool = True,
                          worktree_submodules=None) -> bool:
        """Remove a git worktree path and optionally delete its branch."""
        result = await self.remove_path_result(
            repo_root,
            worktree_path,
            branch=branch,
            name=name,
            force=force,
            worktree_submodules=worktree_submodules,
        )
        return bool(result.get("worktree_removed"))

    @staticmethod
    def _same_worktree_path(left: str, right: str) -> bool:
        if not left or not right:
            return False
        try:
            return os.path.realpath(os.path.expanduser(left)) == os.path.realpath(
                os.path.expanduser(right)
            )
        except Exception:
            return str(left or "") == str(right or "")

    async def removal_state(self, repo_root: str, worktree_path: str, *,
                            branch: str = "") -> dict:
        """Return verified on-disk/git state for a worktree removal target."""
        path_exists = bool(worktree_path) and os.path.lexists(worktree_path)
        entries = await self.list_worktrees(repo_root)
        listed_entries = [
            entry for entry in entries
            if self._same_worktree_path(
                str(entry.get("path", "") or ""),
                worktree_path,
            )
        ]
        branch_entries = [
            entry for entry in entries
            if branch and str(entry.get("branch", "") or "") == branch
        ]
        branch_exists = False
        if branch:
            branch_exists = await self._branch_exists(repo_root, branch)
        return {
            "path": worktree_path,
            "path_exists": path_exists,
            "listed": bool(listed_entries),
            "listed_entries": listed_entries,
            "branch": branch,
            "branch_exists": branch_exists,
            "branch_worktree_entries": branch_entries,
        }

    async def remove_path_result(self, repo_root: str, worktree_path: str, *,
                                 branch: str = "",
                                 name: str = "",
                                 force: bool = True,
                                 worktree_submodules=None) -> dict:
        """Remove a git worktree and verify the real post-state.

        ``git worktree remove`` can fail or, in some busy/attached cases,
        appear to succeed without the worktree disappearing. Treat the git
        subprocess return code as an input, not as ground truth: after the
        command finishes, re-read ``git worktree list`` plus path/branch state
        and report any mismatch explicitly.
        """
        if not worktree_path:
            return {
                "ok": True,
                "worktree_removed": True,
                "branch_deleted": not bool(branch),
                "skipped": True,
                "message": "No worktree path configured",
                "pre_state": {},
                "post_state": {},
                "nested_submodules": [],
                "mismatches": [],
            }
        display_name = name or branch or worktree_path
        result = {
            "ok": False,
            "worktree_removed": False,
            "branch_deleted": not bool(branch),
            "skipped": False,
            "path": worktree_path,
            "branch": branch,
            "git_returncode": None,
            "git_stdout": "",
            "git_stderr": "",
            "branch_delete_returncode": None,
            "branch_delete_stderr": "",
            "pre_state": {},
            "post_state": {},
            "deinit": {},
            "nested_submodules": [],
            "mismatches": [],
            "message": "",
        }
        result["pre_state"] = await self.removal_state(
            repo_root,
            worktree_path,
            branch=branch,
        )
        submodule_paths = _normalize_worktree_submodules(worktree_submodules)
        if submodule_paths:
            result["nested_submodules"] = (
                await self._remove_nested_submodule_worktrees(
                    repo_root,
                    worktree_path,
                    submodule_paths,
                    force=force,
                )
            )
            failed_nested = [
                item for item in result["nested_submodules"]
                if not item.get("ok")
            ]
            if failed_nested:
                result["post_state"] = await self.removal_state(
                    repo_root,
                    worktree_path,
                    branch=branch,
                )
                result["mismatches"].append("nested_submodule_remove_failed")
                result["message"] = (
                    "Nested submodule worktree removal failed; "
                    "superproject worktree was preserved"
                )
                return result
            deinit_paths = [
                str(item.get("path", "") or "").strip()
                for item in result["nested_submodules"]
                if item.get("worktree_removed") and item.get("path")
            ]
            if deinit_paths:
                deinit = await self._safe_deinit_submodules_for_remove(
                    worktree_path,
                    deinit_paths,
                )
                result["deinit"] = deinit
                if not deinit.get("ok"):
                    result["post_state"] = await self.removal_state(
                        repo_root,
                        worktree_path,
                        branch=branch,
                    )
                    result["mismatches"].append("submodule_deinit_failed")
                    result["message"] = (
                        "Submodule deinit failed after nested worktree "
                        "cleanup; superproject worktree was preserved"
                    )
                    return result
        try:
            cmd = ["git", "-C", repo_root,
                   "worktree", "remove", worktree_path]
            if force:
                cmd.append("--force")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            result["git_returncode"] = proc.returncode
            result["git_stdout"] = stdout.decode(errors="replace").strip()
            result["git_stderr"] = stderr.decode(errors="replace").strip()
            if proc.returncode != 0:
                log.warning("git worktree remove failed for '%s': %s",
                            display_name, result["git_stderr"])
            else:
                log.info("Removed worktree for '%s': %s",
                         display_name, worktree_path)
        except Exception:
            log.exception("Failed to remove worktree for '%s'", display_name)
            result["git_returncode"] = -1

        mid_state = await self.removal_state(
            repo_root,
            worktree_path,
            branch=branch,
        )
        worktree_removed = (
            not bool(mid_state.get("path_exists"))
            and not bool(mid_state.get("listed"))
        )

        if branch and worktree_removed:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", repo_root,
                    "branch", "-d", branch,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                result["branch_delete_returncode"] = proc.returncode
                result["branch_delete_stderr"] = (
                    stderr.decode(errors="replace").strip()
                )
                if proc.returncode != 0:
                    log.warning(
                        "git branch -d failed for '%s' after worktree removal: %s",
                        branch,
                        result["branch_delete_stderr"],
                    )
            except Exception:
                log.debug("Could not delete branch %s", branch)
                result["branch_delete_returncode"] = -1
            try:
                from .worktree_streams import invalidate_branch_exists_cache
                invalidate_branch_exists_cache(repo_root, branch)
            except Exception:
                log.debug("Failed to invalidate branch cache", exc_info=True)

        result["post_state"] = await self.removal_state(
            repo_root,
            worktree_path,
            branch=branch,
        )
        result["worktree_removed"] = (
            not bool(result["post_state"].get("path_exists"))
            and not bool(result["post_state"].get("listed"))
        )
        result["branch_deleted"] = (
            not bool(branch)
            or not bool(result["post_state"].get("branch_exists"))
        )

        git_succeeded = result["git_returncode"] == 0
        if git_succeeded and not result["worktree_removed"]:
            result["mismatches"].append("reported_removed_but_present")
            log.warning(
                "git worktree remove reported success for '%s' but post-state "
                "still has path/list entry: path_exists=%s listed=%s",
                display_name,
                result["post_state"].get("path_exists"),
                result["post_state"].get("listed"),
            )
        if not git_succeeded and result["worktree_removed"]:
            result["mismatches"].append("reported_failed_but_gone")
            log.warning(
                "git worktree remove reported failure for '%s' but post-state "
                "shows the worktree is gone",
                display_name,
            )
        if branch and result["worktree_removed"] and not result["branch_deleted"]:
            result["mismatches"].append("branch_delete_failed")

        result["ok"] = bool(
            result["worktree_removed"] and result["branch_deleted"]
        )
        if result["ok"]:
            result["message"] = "Worktree removed"
        elif result["worktree_removed"]:
            result["message"] = (
                "Worktree removed, but branch deletion did not complete"
            )
        else:
            result["message"] = (
                "Worktree removal did not take; path or git worktree entry "
                "is still present"
            )
        return result

    async def prune_admin(self, repo_root: str) -> bool:
        """Prune stale git-worktree admin records for a repository."""
        if not repo_root:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "worktree", "prune",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            log.debug("Could not prune worktree admin for %s", repo_root)
            return False

    async def remove_result(self, cell, force: bool = True,
                            worktree_submodules=None) -> dict:
        """Remove the git worktree/branch for a cell and verify post-state.

        Args:
            cell: AgentCell whose worktree to remove.
            force: If True, force-remove even with uncommitted changes.

        Returns:
            Structured removal status. ``worktree_removed`` reflects verified
            ground truth; ``ok`` additionally requires requested branch cleanup.
        """
        if not cell.worktree_path:
            return {
                "ok": True,
                "worktree_removed": True,
                "branch_deleted": True,
                "skipped": True,
                "message": "No worktree path configured",
                "pre_state": {},
                "post_state": {},
                "nested_submodules": [],
                "mismatches": [],
            }

        # Resolve repo root — needed for git commands
        repo_root = cell.worktree_repo_root
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path)
        if not repo_root:
            log.warning("Cannot find repo root for worktree '%s' — "
                        "trying parent directory", cell.name)
            repo_root = os.path.dirname(cell.worktree_path)

        result = await self.remove_path_result(
            repo_root,
            cell.worktree_path,
            branch=cell.worktree_branch,
            name=cell.name,
            force=force,
            worktree_submodules=worktree_submodules,
        )

        if result.get("worktree_removed"):
            cell.worktree_path = ""
            cell.worktree_branch = ""
            cell.worktree_repo_root = ""
            cell.worktree_base_branch = ""
            cell.worktree_dirty = False
            cell.worktree_diff = {}
            cell.worktree_changed_files = []
            cell.worktree_checkpoints = 0
            cell.worktree_ahead = 0
            cell.worktree_behind = 0
            cell.worktree_merged = False

        return result

    async def remove(self, cell, force: bool = True,
                     worktree_submodules=None) -> bool:
        """Remove the git worktree and branch associated with a cell.

        Args:
            cell: AgentCell whose worktree to remove.
            force: If True, force-remove even with uncommitted changes.

        Returns:
            True if successfully removed, False otherwise.
        """
        result = await self.remove_result(
            cell,
            force=force,
            worktree_submodules=worktree_submodules,
        )
        return bool(result.get("worktree_removed"))

    @staticmethod
    def _porcelain_v1_path(raw: str) -> str:
        if not raw:
            return ""
        if raw.startswith(("?? ", "!! ")):
            return _normalize_repo_rel_path(raw[3:])
        if len(raw) < 4:
            return ""
        path = raw[3:]
        if "\t" in path:
            path = path.split("\t", 1)[0]
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        return _normalize_repo_rel_path(path)

    async def _is_clean_submodule_gitlink_drift(
            self,
            cell,
            path: str,
            allowed_paths: set[str],
            *,
            status_xy: str = "",
    ) -> bool:
        """True when a status entry is only clean submodule HEAD drift.

        Linked nested submodule worktrees can transiently move their HEAD while
        the superproject index remains unchanged. Git reports that as a dirty
        submodule path even when the submodule worktree itself is clean and the
        two submodule commits have identical file content. That structural
        gitlink-only drift should not block safe worktree removal.
        """
        wt_path = str(getattr(cell, "worktree_path", "") or "").strip()
        norm_path = _normalize_repo_rel_path(path)
        if not wt_path or not norm_path or norm_path not in allowed_paths:
            return False

        if status_xy:
            # Only ignore unstaged/worktree-side submodule drift. A staged
            # gitlink update is real superproject content and must stay dirty.
            x_status = status_xy[0] if len(status_xy) >= 1 else ""
            if x_status not in {" ", "."}:
                return False

        sub_wt = self._join_repo_rel(wt_path, norm_path)
        if not os.path.isdir(sub_wt):
            return False

        cached_code, _cached_out, _cached_err = await self._git_run(
            wt_path,
            "diff",
            "--cached",
            "--quiet",
            "--",
            norm_path,
        )
        if cached_code != 0:
            return False

        raw_code, raw_out = await self._git_stdout(
            wt_path,
            "diff",
            "--raw",
            "--no-renames",
            "--",
            norm_path,
        )
        if raw_code != 0 or not raw_out.strip():
            return False
        for line in raw_out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                meta, raw_path = line.split("\t", 1)
            except ValueError:
                return False
            parts = meta.split()
            if len(parts) < 5:
                return False
            old_mode = parts[0].lstrip(":")
            new_mode = parts[1]
            status = parts[4]
            if (
                    old_mode != "160000"
                    or new_mode != "160000"
                    or not status.startswith("M")
                    or _normalize_repo_rel_path(raw_path) != norm_path):
                return False

        diff_code, diff_out = await self._git_stdout(
            wt_path,
            "diff",
            "--submodule=diff",
            "--",
            norm_path,
        )
        if diff_code != 0:
            return False
        # A content-free submodule commit range prints only the summary header
        # ("Submodule path old..new:") on some Git versions and nothing on
        # others. Any actual file delta emits normal diff hunks and is dirty.
        for line in diff_out.splitlines():
            if line.strip() and not line.startswith("Submodule "):
                return False

        status_code, sub_status = await self._git_stdout(
            sub_wt,
            "status",
            "--porcelain",
        )
        return status_code == 0 and not sub_status.strip()

    async def _status_lines_are_only_clean_submodule_gitlink_drift(
            self,
            cell,
            lines: list[str],
            allowed_paths: set[str],
    ) -> bool:
        if not lines or not allowed_paths:
            return False
        for raw in lines:
            if raw.startswith(("?? ", "!! ")):
                return False
            path = self._porcelain_v1_path(raw)
            status_xy = raw[:2] if len(raw) >= 2 else ""
            if not await self._is_clean_submodule_gitlink_drift(
                    cell,
                    path,
                    allowed_paths,
                    status_xy=status_xy,
            ):
                return False
        return True

    async def validate(self, cell) -> bool:
        """Check that a cell's worktree_path exists and is a valid worktree."""
        if not cell.worktree_path:
            return False
        if not os.path.isdir(cell.worktree_path):
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
                "rev-parse", "--is-inside-work-tree",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    async def has_uncommitted_changes(self, cell,
                                      worktree_submodules=None) -> bool:
        """True if the worktree has staged, unstaged, or untracked changes."""
        if not cell.worktree_path:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
                "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            submodule_paths = _normalize_worktree_submodules(
                worktree_submodules
            )
            status_lines = [
                line for line in stdout.decode().splitlines()
                if line.strip()
            ]
            if status_lines:
                if not await self._status_lines_are_only_clean_submodule_gitlink_drift(
                        cell,
                        status_lines,
                        set(submodule_paths),
                ):
                    return True
            if not submodule_paths:
                return False
            infos = await self._nested_submodule_infos_for_cell(
                cell,
                submodule_paths,
                require_worktree=True,
                strict=False,
            )
            for info in infos:
                code, status = await self._git_stdout(
                    info["worktree_path"],
                    "status",
                    "--porcelain",
                )
                if code == 0 and status.strip():
                    return True
            return False
        except Exception:
            return False

    async def diff_summary(self, cell, *, non_test_only: bool = False) -> dict:
        """Return diff stats for the worktree vs its base branch.

        Returns:
            {"files": int, "insertions": int, "deletions": int}
            Empty dict on failure.

        If ``non_test_only`` is true, test directories and common test-file
        suffixes are excluded from the totals.
        """
        if not cell.worktree_path or not cell.worktree_base_branch:
            return {}
        try:
            # Diff against the merge base with the base branch
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
                "diff", "--stat", "--numstat",
                f"{cell.worktree_base_branch}...HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return {}

            summary, _paths = _numstat_summary(
                stdout.decode(),
                non_test_only=non_test_only,
            )
            return summary
        except Exception:
            log.debug("diff_summary failed for '%s'", cell.name)
            return {}

    async def diff_files_summary(self, cell,
                                 paths: list[str] | None = None,
                                 *, scope_domain: str | None = None) -> dict:
        """Return structured per-file diff summary for review planning.

        ``scope_domain`` (optional) is the task's declared domain, used only to
        add an observability-only ``out_of_scope`` signal/field when the diff
        reaches into a clearly-foreign domain (TORQUE:604 A2). It never blocks
        or changes any behavior; when ``None`` no scope flag is computed.
        """
        if not cell.worktree_path or not cell.worktree_base_branch:
            return {}
        path_filters = list(paths or [])
        base_ref = f"{cell.worktree_base_branch}...HEAD"
        try:
            async def _run(*extra_args):
                args = [
                    "git", "-C", cell.worktree_path, "diff",
                    "--find-renames", *extra_args, base_ref,
                ]
                if path_filters:
                    args.append("--")
                    args.extend(path_filters)
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode != 0:
                    return b""
                return stdout

            numstat_records = _parse_numstat_z(
                await _run("--numstat", "-z")
            )
            status_records = _parse_name_status_z(
                await _run("--name-status", "-z")
            )

            status_by_key = {
                _diff_summary_key(record): record for record in status_records
            }
            files = []
            for record in numstat_records:
                status_record = status_by_key.pop(
                    _diff_summary_key(record),
                    {},
                )
                path = status_record.get("path") or record.get("path", "")
                old_path = (
                    status_record.get("old_path")
                    or record.get("old_path", "")
                )
                status = status_record.get("status") or "modified"
                file_info = {
                    "path": path,
                    "old_path": old_path,
                    "status": status,
                    "insertions": record.get("insertions", 0),
                    "deletions": record.get("deletions", 0),
                    "binary": bool(record.get("binary")),
                }
                file_info["signals"] = _diff_signals(
                    file_info["path"],
                    old_path=file_info["old_path"],
                    status=file_info["status"],
                    insertions=file_info["insertions"],
                    deletions=file_info["deletions"],
                    binary=file_info["binary"],
                )
                files.append(file_info)

            for status_record in status_by_key.values():
                file_info = {
                    "path": status_record.get("path", ""),
                    "old_path": status_record.get("old_path", ""),
                    "status": status_record.get("status", "modified"),
                    "insertions": 0,
                    "deletions": 0,
                    "binary": False,
                }
                file_info["signals"] = _diff_signals(
                    file_info["path"],
                    old_path=file_info["old_path"],
                    status=file_info["status"],
                )
                files.append(file_info)

            # Tag out-of-scope files before tallying so the signal flows into
            # interesting_files / signal_counts naturally (observability only).
            out_of_scope = out_of_scope_diff_paths(
                scope_domain, [f["path"] for f in files]
            )
            if out_of_scope:
                foreign = set(out_of_scope)
                for file_info in files:
                    if file_info["path"] in foreign \
                            and "out_of_scope" not in file_info["signals"]:
                        file_info["signals"].append("out_of_scope")

            stats = {
                "files": len(files),
                "insertions": sum(f["insertions"] for f in files),
                "deletions": sum(f["deletions"] for f in files),
            }
            interesting_files = [
                {"path": f["path"], "signals": f["signals"]}
                for f in files
                if f["signals"]
            ]
            signal_counts: dict[str, int] = {}
            for file_info in files:
                for signal in file_info["signals"]:
                    signal_counts[signal] = signal_counts.get(signal, 0) + 1

            summary = {
                "stats": stats,
                "files": files,
                "interesting_files": interesting_files,
                "signal_counts": signal_counts,
            }
            if out_of_scope:
                summary["out_of_scope"] = {
                    "domain": scope_domain,
                    "paths": out_of_scope,
                    "count": len(out_of_scope),
                    "digest_line": (
                        f"diff touches {len(out_of_scope)} file(s) outside "
                        f"declared {scope_domain} scope: "
                        f"{', '.join(out_of_scope)}"
                    ),
                }
            return summary
        except Exception:
            log.debug("diff_files_summary failed for '%s'", cell.name)
            return {}

    async def changed_files(self, cell) -> list[str]:
        """Return live changed file paths for the worktree vs its base branch.

        Includes committed branch diff plus staged/unstaged tracked changes
        and untracked files so callers see the full working-tree surface.
        """
        if not cell.worktree_path or not cell.worktree_base_branch:
            return []
        try:
            changed = set()

            async def _collect(*args):
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", cell.worktree_path,
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode != 0:
                    return
                for line in stdout.decode().splitlines():
                    path = line.strip()
                    if path:
                        changed.add(path)

            # Committed work on the branch since it forked from base.
            await _collect("diff", "--name-only",
                           f"{cell.worktree_base_branch}...HEAD")
            # Staged + unstaged tracked edits not yet committed.
            await _collect("diff", "--name-only", "HEAD")
            # Untracked files that would not appear in git diff.
            await _collect("ls-files", "--others", "--exclude-standard")

            return sorted(changed)
        except Exception:
            log.debug("changed_files failed for '%s'", cell.name)
            return []

    def _isolated_worktree_dir(self, cell, op: str) -> Optional[str]:
        """Return the cell's isolated worktree dir, or None to refuse.

        Fail-closed companion to the pre-commit guard hook: a mutating git
        op Torque runs on behalf of an agent must target that agent's own
        worktree, never the shared main checkout. If ``worktree_path`` is
        empty or has somehow collapsed onto the repo root, refuse the op and
        log loudly rather than risk contaminating the shared checkout
        (TORQUE:580).
        """
        wt = (getattr(cell, "worktree_path", "") or "").strip()
        if not wt:
            return None
        if worktree_dir_is_shared_checkout(cell):
            log.error(
                "ISOLATION GUARD: refusing worker git %s for '%s' — "
                "worktree_path %r resolves to the shared main checkout %r. "
                "This would contaminate the shared checkout (TORQUE:580).",
                op, getattr(cell, "name", ""), wt,
                getattr(cell, "worktree_repo_root", ""),
            )
            return None
        return wt

    async def _checkpoint_nested_submodules(self, repo_root: str, wt_dir: str,
                                            worktree_submodules,
                                            message: str) -> list[dict]:
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return []
        results: list[dict] = []
        infos = await self._nested_submodule_infos(
            repo_root,
            wt_dir,
            paths,
            ref="HEAD",
            require_worktree=True,
            strict=False,
        )
        for info in infos:
            sub_wt = info["worktree_path"]
            entry = {
                "path": info["path"],
                "worktree_path": sub_wt,
                "committed": False,
                "sha": "",
                "message": "",
            }
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", sub_wt, "add", "-A",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", sub_wt, "diff", "--cached", "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode == 0:
                entry["message"] = "No nested submodule changes"
                results.append(entry)
                continue
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", sub_wt, "commit", "-m", message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    "Nested submodule checkpoint failed for "
                    f"{info['path']}: {stderr.decode(errors='replace').strip()}"
                )
            sha = await self.rev_parse(sub_wt, "HEAD") or ""
            entry.update({
                "committed": True,
                "sha": sha,
                "message": "Nested submodule checkpoint committed",
            })
            results.append(entry)
            log.info(
                "Checkpointed nested submodule %s for worktree %s: %s",
                info["path"],
                wt_dir,
                sha[:8],
            )
        return results

    async def _guard_checkpoint_nested_gitlinks(self, repo_root: str,
                                               wt_dir: str,
                                               base_branch: str,
                                               worktree_submodules,
                                               checkpoint_results: list[dict]
                                               | None = None) -> list[dict]:
        """Reset clean, uncheckpointed nested HEAD drift before super add -A.

        A broad superproject ``git add -A`` records a submodule gitlink whenever
        the nested checkout's HEAD differs from the current superproject gitlink,
        even when the worker never edited that submodule.  Only nested submodules
        that this checkpoint just committed are allowed to bump their
        superproject gitlink; clean incidental drift is preserved on a backup
        branch, then reset to the current superproject gitlink so the checkpoint
        cannot capture it.
        """
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return []
        committed_paths = {
            _normalize_repo_rel_path(item.get("path", ""))
            for item in (checkpoint_results or [])
            if item.get("committed")
        }
        infos = await self._nested_submodule_infos(
            repo_root,
            wt_dir,
            paths,
            ref="HEAD",
            require_worktree=True,
            strict=False,
        )
        guarded: list[dict] = []
        for info in infos:
            path = info["path"]
            if path in committed_paths:
                continue
            current_sha = info.get("gitlink_sha", "")
            if not current_sha:
                current_sha = await self._gitlink_sha(wt_dir, "HEAD", path)
            base_sha = await self._gitlink_sha(wt_dir, base_branch, path)
            head = await self.rev_parse(info["worktree_path"], "HEAD") or ""
            entry = {
                "path": path,
                "worktree_path": info["worktree_path"],
                "head": head,
                "current_gitlink": current_sha,
                "base_gitlink": base_sha,
                "reset": False,
                "preserve_branch": "",
                "message": "",
            }
            if not current_sha or not head or head == current_sha:
                continue
            code, status = await self._git_stdout(
                info["worktree_path"],
                "status",
                "--porcelain",
            )
            if code != 0:
                entry["message"] = "Could not inspect nested submodule status"
                guarded.append(entry)
                continue
            if status.strip():
                # _checkpoint_nested_submodules should have handled dirty
                # nested work before this guard. Avoid discarding anything if a
                # submodule is still unexpectedly dirty.
                entry["message"] = "Nested submodule still dirty; not reset"
                guarded.append(entry)
                continue
            preserved = await self._create_preserved_nested_submodule_ref(
                info,
                head,
            )
            entry["preserve_branch"] = preserved.get("branch", "")
            code, _out, err = await self._git_run(
                info["worktree_path"],
                "reset",
                "--hard",
                current_sha,
            )
            if code != 0:
                raise RuntimeError(
                    "Could not reset clean nested submodule drift for "
                    f"{path} to current gitlink {current_sha[:12]}: {err}"
                )
            await self._git_run(wt_dir, "reset", "-q", "--", path)
            entry["reset"] = True
            entry["message"] = (
                "Reset clean nested submodule HEAD drift to current gitlink"
            )
            guarded.append(entry)
            log.warning(
                "Reset clean nested submodule drift before checkpoint: "
                "%s %s -> %s (preserved on %s)",
                path,
                head[:12],
                current_sha[:12],
                entry["preserve_branch"],
            )
        return guarded

    async def _assert_nested_gitlinks_match_heads(self, wt_dir: str,
                                                  worktree_submodules) -> bool:
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return True
        for sub_path in paths:
            sub_wt = self._join_repo_rel(wt_dir, sub_path)
            if not await self._is_git_repo(sub_wt):
                continue
            head = await self.rev_parse(sub_wt, "HEAD") or ""
            gitlink = await self._gitlink_sha(wt_dir, "HEAD", sub_path)
            if head and gitlink and head != gitlink:
                log.error(
                    "Nested submodule gitlink mismatch for %s: "
                    "superproject=%s submodule=%s",
                    sub_path,
                    gitlink,
                    head,
                )
                return False
        return True

    async def checkpoint(self, cell, message: str = "",
                         worktree_submodules=None) -> Optional[str]:
        """Auto-commit all changes in the worktree. Returns commit SHA."""
        wt_dir = self._isolated_worktree_dir(cell, "checkpoint commit")
        if not wt_dir:
            return None
        try:
            # Seed checkpoint counter from git history if not yet set
            if cell.worktree_checkpoints == 0:
                cell.worktree_checkpoints = await self.count_commits(cell)

            if not message:
                n = cell.worktree_checkpoints + 1
                message = f"torque: checkpoint {n} — {cell.name}"

            repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
            if not repo_root:
                repo_root = await self.get_repo_root(wt_dir) or ""
            nested_checkpoint_results: list[dict] = []
            if _normalize_worktree_submodules(worktree_submodules):
                nested_checkpoint_results = await self._checkpoint_nested_submodules(
                    repo_root,
                    wt_dir,
                    worktree_submodules,
                    message,
                )
                await self._guard_checkpoint_nested_gitlinks(
                    repo_root,
                    wt_dir,
                    getattr(cell, "worktree_base_branch", "") or "HEAD",
                    worktree_submodules,
                    nested_checkpoint_results,
                )

            # Stage everything
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", wt_dir, "add", "-A",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            # Check if there's anything to commit
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", wt_dir,
                "diff", "--cached", "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode == 0:
                log.debug("No changes to checkpoint for '%s'", cell.name)
                return None

            proc = await asyncio.create_subprocess_exec(
                "git", "-C", wt_dir,
                "commit", "-m", message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                log.warning("Checkpoint commit failed for '%s': %s",
                            cell.name, stderr.decode().strip())
                return None

            # Get the SHA
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", wt_dir,
                "rev-parse", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            sha = stdout.decode().strip()
            if not await self._assert_nested_gitlinks_match_heads(
                    wt_dir,
                    worktree_submodules,
            ):
                return None
            cell.worktree_checkpoints += 1
            log.info("Checkpoint %d for '%s': %s",
                     cell.worktree_checkpoints, cell.name, sha[:8])
            return sha
        except Exception:
            log.exception("Checkpoint failed for '%s'", cell.name)
            return None

    async def current_head(self, cell) -> Optional[str]:
        """Return the current HEAD SHA for a worktree."""
        if not cell.worktree_path:
            return None
        return await self.rev_parse(cell.worktree_path, "HEAD")

    async def list_checkpoints(self, cell) -> list[dict]:
        """List commits on the worktree branch since the fork point.

        Returns:
            List of {sha, short_sha, message, date, insertions, deletions}
            dicts, newest first.
        """
        if not cell.worktree_path or not cell.worktree_base_branch:
            return []
        try:
            # Use unique delimiters to separate header fields from body
            hdr_delim = "---TORQUE_COMMIT---"
            body_delim = "---TORQUE_BODY---"
            body_end = "---TORQUE_BODY_END---"
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
                "log",
                f"--format={hdr_delim}%n%H%n%h%n%s%n%aI%n{body_delim}%n%b{body_end}",
                "--numstat",
                f"{cell.worktree_base_branch}..HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []

            commits = []
            # Split by header delimiter — first chunk is empty
            chunks = stdout.decode().split(hdr_delim)[1:]
            for chunk in chunks:
                # Extract body between body delimiters
                body = ""
                if body_delim in chunk and body_end in chunk:
                    body_start = chunk.index(body_delim) + len(body_delim)
                    b_end = chunk.index(body_end)
                    body = chunk[body_start:b_end].strip()
                    # Remove body section from chunk for numstat parsing
                    chunk = (chunk[:chunk.index(body_delim)]
                             + chunk[b_end + len(body_end):])

                lines = chunk.strip().splitlines()
                if len(lines) < 4:
                    continue
                ins = 0
                dels = 0
                for stat_line in lines[4:]:
                    parts = stat_line.split("\t")
                    if len(parts) >= 2:
                        try:
                            ins += int(parts[0]) if parts[0] != "-" else 0
                            dels += int(parts[1]) if parts[1] != "-" else 0
                        except ValueError:
                            continue
                commits.append({
                    "sha": lines[0],
                    "short_sha": lines[1],
                    "message": lines[2],
                    "body": body,
                    "date": lines[3],
                    "insertions": ins,
                    "deletions": dels,
                })
            return commits
        except Exception:
            log.debug("list_checkpoints failed for '%s'", cell.name)
            return []

    async def rollback(self, cell, commit_sha: str) -> bool:
        """Reset the worktree branch to the given commit.

        Returns True on success.
        """
        wt_dir = self._isolated_worktree_dir(cell, "rollback reset --hard")
        if not wt_dir:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", wt_dir,
                "reset", "--hard", commit_sha,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                log.warning("Rollback failed for '%s': %s",
                            cell.name, stderr.decode().strip())
                return False
            log.info("Rolled back '%s' to %s", cell.name, commit_sha[:8])
            return True
        except Exception:
            log.exception("Rollback failed for '%s'", cell.name)
            return False

    async def is_merged(self, cell, worktree_submodules=None) -> bool:
        """Check if the worktree branch has been merged into the base branch.

        Handles both regular merges (ancestry check) and squash merges
        (merge simulation via ``git merge-tree --write-tree``).
        """
        if not cell.worktree_path or not cell.worktree_base_branch:
            return False
        repo_root = cell.worktree_repo_root
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path)
        if not repo_root:
            return False
        try:
            # Guard: if branch hasn't diverged from base, nothing to merge
            _code, out, _err = await self._refresh_git(
                repo_root,
                "rev-parse",
                cell.worktree_branch, cell.worktree_base_branch,
            )
            shas = out.split()
            if len(shas) == 2 and shas[0] == shas[1]:
                return False

            # Fast path: check if worktree branch is an ancestor of base
            returncode, _out, _err = await self._refresh_git(
                repo_root,
                "merge-base", "--is-ancestor",
                cell.worktree_branch, cell.worktree_base_branch,
                check=False,
            )
            if returncode == 0:
                return await self._nested_submodule_branches_merged(
                    cell,
                    repo_root,
                    worktree_submodules,
                )

            # Slow path: detect squash merges by simulating a merge.
            # If re-merging the branch into base produces base's exact
            # tree, the branch's changes are already incorporated
            # (squash merge, cherry-pick, etc.).  Unlike a direct tip
            # comparison, this works even when base has diverged.

            # 1. Get base branch tree SHA
            _code, stdout, _err = await self._refresh_git(
                repo_root,
                "rev-parse",
                f"{cell.worktree_base_branch}^{{tree}}",
            )
            base_tree = stdout.strip()

            # 2. Simulate merging branch into base (git 2.38+)
            _code, stdout, _err = await self._refresh_git(
                repo_root,
                "merge-tree", "--write-tree",
                cell.worktree_base_branch, cell.worktree_branch,
            )
            merge_tree = stdout.strip().split('\n')[0]

            # 3. If the simulated merge produces base's tree unchanged,
            #    the branch's changes are already in base.
            super_merged = merge_tree == base_tree
            if not super_merged:
                return False
            return await self._nested_submodule_branches_merged(
                cell,
                repo_root,
                worktree_submodules,
            )
        except WorktreeRefreshError:
            raise
        except Exception:
            log.debug("is_merged check failed for '%s'", cell.name)
            return False

    async def _nested_submodule_branches_merged(self, cell, repo_root: str,
                                                worktree_submodules=None) -> bool:
        submodule_paths = _normalize_worktree_submodules(worktree_submodules)
        if not submodule_paths:
            return True
        infos = await self._nested_submodule_infos(
            repo_root,
            cell.worktree_path,
            submodule_paths,
            ref="HEAD",
            require_worktree=True,
            strict=False,
        )
        base = str(getattr(cell, "worktree_base_branch", "") or "").strip()
        for info in infos:
            sub_branch = await self.get_current_branch(info["worktree_path"])
            if not sub_branch or sub_branch == "HEAD":
                continue
            if not await self.is_branch_merged(
                    info["module_dir"],
                    branch=sub_branch,
                    base_branch=base,
            ):
                return False
        return True

    async def is_branch_merged(self, repo_root: str, *,
                               branch: str,
                               base_branch: str) -> bool:
        """Check cleanup merge status for a branch without a live cell.

        Treat a branch tip that already equals the base tip as merged so stale
        worktrees reset to base remain safely prunable.
        """
        branch_sha = await self.rev_parse(repo_root, branch)
        base_sha = await self.rev_parse(repo_root, base_branch)
        if branch_sha and branch_sha == base_sha:
            return True
        probe = type("WorktreeProbe", (), {
            "name": branch or "worktree",
            "worktree_path": repo_root,
            "worktree_repo_root": repo_root,
            "worktree_branch": branch,
            "worktree_base_branch": base_branch,
        })()
        return await self.is_merged(probe)

    async def check_base_advanced(self, cell, pre_merge_sha: str) -> bool:
        """Fallback merge check: did the base branch advance since
        *pre_merge_sha* and do the new commits include all files the
        branch changed?

        This catches squash merges that ``is_merged`` can't verify
        because of overlapping changes between the branch and other
        commits on base.
        """
        if not cell.worktree_path or not cell.worktree_base_branch:
            return False
        repo_root = cell.worktree_repo_root
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path)
        if not repo_root or not pre_merge_sha:
            return False
        try:
            base = cell.worktree_base_branch

            # Current base SHA
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root, "rev-parse", base,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return False
            current_sha = stdout.decode().strip()
            if current_sha == pre_merge_sha:
                return False  # base didn't move

            # Files changed on base since pre-merge
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root, "diff", "--name-only",
                pre_merge_sha, current_sha,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return False
            base_files = set(stdout.decode().strip().splitlines())

            # Files changed by the branch (fork → branch tip)
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "merge-base", base, cell.worktree_branch,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return False
            fork = stdout.decode().strip()

            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root, "diff", "--name-only",
                fork, cell.worktree_branch,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return False
            branch_files = set(stdout.decode().strip().splitlines())

            if not branch_files:
                return False

            # Every file the branch touched must appear in base's new changes
            return branch_files <= base_files
        except Exception:
            log.debug("check_base_advanced failed for '%s'", cell.name)
            return False

    async def _blob_size(self, repo_root: str, blob_sha: str) -> int:
        if not repo_root or not blob_sha:
            return 0
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "cat-file", "-s", blob_sha,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return int(stdout.decode().strip())
        except Exception:
            log.debug("Could not read blob size %s in %s", blob_sha, repo_root)
        return 0

    async def _parse_merge_tree_conflicts(self, output: str, *,
                                          repo_root: str = "",
                                          base_label: str = "",
                                          branch_label: str = "") -> list[dict]:
        """Parse conflict info from ``git merge-tree`` output."""
        conflicts: list[dict] = []
        stage_blobs: dict[str, dict[int, str]] = {}
        binary_paths: set[str] = set()

        for raw_line in output.splitlines():
            line = raw_line.strip()
            stage_match = re.match(
                r"^\d{6}\s+([0-9a-f]{40})\s+([123])\t(.+)$",
                line,
            )
            if stage_match:
                blob_sha, stage, path = stage_match.groups()
                norm_path = _normalize_repo_rel_path(path)
                if norm_path:
                    stage_blobs.setdefault(norm_path, {})[int(stage)] = blob_sha
                continue
            binary_prefix = "warning: Cannot merge binary files: "
            if line.startswith(binary_prefix):
                path = line[len(binary_prefix):].split(" (", 1)[0].strip()
                norm_path = _normalize_repo_rel_path(path)
                if norm_path:
                    binary_paths.add(norm_path)

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line.startswith("CONFLICT"):
                continue
            reason = ""
            path = ""
            paren_start = line.find("(")
            paren_end = line.find(")")
            if paren_start != -1 and paren_end != -1:
                reason = line[paren_start + 1:paren_end]
            if "Merge conflict in " in line:
                path = line.split("Merge conflict in ", 1)[1].strip()
            elif " deleted in " in line:
                colon_pos = line.find(": ")
                if colon_pos != -1:
                    rest = line[colon_pos + 2:]
                    path = rest.split(" deleted in ")[0].strip()
            else:
                colon_pos = line.find(": ")
                if colon_pos != -1:
                    rest = line[colon_pos + 2:].strip()
                    parts = rest.rsplit(" ", 1)
                    path = parts[-1] if parts else rest
            norm_path = _normalize_repo_rel_path(path)
            conflict = {"path": norm_path, "reason": reason}
            if norm_path in binary_paths:
                ours_sha = stage_blobs.get(norm_path, {}).get(2, "")
                theirs_sha = stage_blobs.get(norm_path, {}).get(3, "")
                ours_size = await self._blob_size(repo_root, ours_sha)
                theirs_size = await self._blob_size(repo_root, theirs_sha)
                base_name = base_label or "base"
                branch_name = branch_label or "branch"
                detail = (
                    f"binary differs — {base_name}: {ours_size} bytes "
                    f"{ours_sha[:12]}, {branch_name}: {theirs_size} bytes "
                    f"{theirs_sha[:12]}"
                ).strip()
                conflict.update({
                    "binary": True,
                    "ours_blob_sha": ours_sha,
                    "theirs_blob_sha": theirs_sha,
                    "ours_size": ours_size,
                    "theirs_size": theirs_size,
                    "detail": detail,
                })
                if detail:
                    conflict["reason"] = (
                        f"{reason} — {detail}" if reason else detail
                    )
            conflicts.append(conflict)
        return conflicts

    async def check_merge_conflicts(self, cell,
                                    worktree_submodules=None) -> dict:
        """Dry-run merge of worktree branch into base.

        Returns dict with keys:
            clean (bool), tree_sha (str), conflicts (list[dict])
        """
        if not cell.worktree_path or not cell.worktree_base_branch \
                or not cell.worktree_branch:
            return {"clean": False, "tree_sha": "",
                    "conflicts": [], "error": "No worktree or base branch"}
        repo_root = cell.worktree_repo_root
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path)
        if not repo_root:
            return {"clean": False, "tree_sha": "",
                    "conflicts": [], "error": "Cannot find repo root"}
        try:
            submodule_paths = _normalize_worktree_submodules(worktree_submodules)
            if submodule_paths:
                preflight = await self.nested_submodule_merge_preflight(
                    cell,
                    submodule_paths,
                )
                if not preflight.get("ok"):
                    return {
                        "clean": False,
                        "tree_sha": "",
                        "conflicts": [],
                        "error": preflight.get(
                            "error",
                            "Nested submodule merge preflight failed",
                        ),
                        "preflight": preflight,
                    }
            base = cell.worktree_base_branch
            branch = cell.worktree_branch
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "merge-tree", "--write-tree", base, branch,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode(errors="replace")
            stderr_text = stderr.decode(errors="replace").strip()
            if proc.returncode == 0:
                tree_sha = output.strip().split("\n")[0]
                return {"clean": True, "tree_sha": tree_sha,
                        "conflicts": []}
            # Conflicts — parse output
            conflicts = await self._parse_merge_tree_conflicts(
                output,
                repo_root=repo_root,
                base_label=base,
                branch_label=branch,
            )
            result = {"clean": False, "tree_sha": "",
                      "conflicts": conflicts}
            submodule_paths = _normalize_worktree_submodules(worktree_submodules)
            gitlink_conflicts = [
                item for item in conflicts
                if _normalize_repo_rel_path(item.get("path", "")) in submodule_paths
            ]
            if gitlink_conflicts:
                paths = ", ".join(
                    item.get("path", "") for item in gitlink_conflicts
                )
                result["error"] = (
                    "Nested submodule gitlink conflict while merging "
                    f"{branch} into {base}: {paths}. Rebase the worktree "
                    "or merge the nested submodule branch pair first."
                )
            elif not conflicts:
                raw_status = stderr_text or output.strip()
                if raw_status:
                    raw_status = " ".join(
                        line.strip()
                        for line in raw_status.splitlines()
                        if line.strip()
                    )[:500]
                    result["error"] = (
                        "Merge conflict (unparsed paths): "
                        f"{raw_status}"
                    )
                else:
                    result["error"] = "Merge conflict (unparsed paths)."
            return result
        except Exception:
            log.exception("check_merge_conflicts failed for '%s'",
                          cell.name)
            return {"clean": False, "tree_sha": "",
                    "conflicts": [], "error": "Merge check failed"}

    async def server_merge(self, cell, message: str,
                           squash: bool = True,
                           worktree_submodules=None) -> dict:
        """Perform server-side merge of worktree branch into base.

        Uses git plumbing (merge-tree + commit-tree + update-ref)
        for a deterministic, agent-free merge.

        Returns dict with keys: ok (bool), sha (str), error (str)
        """
        repo_root = cell.worktree_repo_root
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path)
        if not repo_root:
            return {"ok": False, "error": "Cannot find repo root"}

        base = cell.worktree_base_branch
        branch = cell.worktree_branch
        submodule_paths = _normalize_worktree_submodules(worktree_submodules)
        if submodule_paths:
            nested_merge = await self._merge_nested_submodules_for_merge(
                cell,
                submodule_paths,
                message=message,
            )
            if not nested_merge.get("ok"):
                return {
                    "ok": False,
                    "error": nested_merge.get(
                        "error",
                        "Nested submodule merge failed",
                    ),
                    "nested_submodules": nested_merge,
                }

        # 1. Verify clean merge (race-condition guard)
        check = await self.check_merge_conflicts(cell)
        if not check["clean"]:
            return {"ok": False,
                    "error": check.get("error", "Conflicts detected")}

        tree_sha = check["tree_sha"]

        try:
            # 2. Get base branch SHA
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "rev-parse", base,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                return {"ok": False,
                        "error": f"Cannot resolve {base}: "
                                 f"{stderr.decode().strip()}"}
            base_sha = stdout.decode().strip()

            # 3. Build commit-tree command
            cmd = ["git", "-C", repo_root,
                   "commit-tree", tree_sha,
                   "-p", base_sha]
            if not squash:
                # Regular merge: add branch as second parent
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", repo_root,
                    "rev-parse", branch,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    return {"ok": False,
                            "error": f"Cannot resolve {branch}: "
                                     f"{stderr.decode().strip()}"}
                branch_sha = stdout.decode().strip()
                cmd.extend(["-p", branch_sha])
            cmd.extend(["-m", message])

            # 4. Create the commit
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                return {"ok": False,
                        "error": f"commit-tree failed: "
                                 f"{stderr.decode().strip()}"}
            new_sha = stdout.decode().strip()

            # 5. Advance base branch to the new commit.
            #    If the main repo has the base branch checked out, use
            #    `merge --ff-only` so the ref, index, AND working tree
            #    are all updated safely (refuses if local changes
            #    conflict).  Otherwise just move the ref — the working
            #    tree belongs to another branch and needs no update.
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "symbolic-ref", "--short", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            checked_out = stdout.decode().strip() \
                if proc.returncode == 0 else ""

            if checked_out == base:
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", repo_root,
                    "merge", "--ff-only", new_sha,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    return {"ok": False,
                            "error": f"merge --ff-only failed: "
                                     f"{stderr.decode().strip()}"}
            else:
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", repo_root,
                    "update-ref", f"refs/heads/{base}", new_sha,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    return {"ok": False,
                            "error": f"update-ref failed: "
                                     f"{stderr.decode().strip()}"}

            log.info("Server-side %s of '%s' (%s) into %s: %s",
                     "squash merge" if squash else "merge",
                     cell.name, branch, base, new_sha[:8])
            result = {"ok": True, "sha": new_sha}
            if submodule_paths:
                result["nested_submodules"] = nested_merge
            return result
        except Exception:
            log.exception("server_merge failed for '%s'", cell.name)
            return {"ok": False, "error": "Server merge failed"}

    async def reset_to_base(self, cell, worktree_submodules=None) -> bool:
        """Reset the worktree branch to the base branch tip.

        Used after merge: all old commits are already incorporated into
        base, so the worktree should start fresh.  This avoids the
        re-merge problem that ``rebase_onto_base`` hits with squash
        merges (where individual commits can't be cleanly replayed on
        top of the squashed result).

        Returns True on success, False on failure.
        """
        if not cell.worktree_base_branch:
            return False
        wt_dir = self._isolated_worktree_dir(cell, "reset_to_base switch")
        if not wt_dir:
            return False
        base = cell.worktree_base_branch
        try:
            # switch -C moves the current branch to <base> and
            # checks it out — a single porcelain command that
            # updates ref + index + working tree.
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", wt_dir,
                "switch", "-C", cell.worktree_branch, base,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode().strip()
                log.warning("reset_to_base failed for '%s': %s",
                            cell.name, err)
                return False
            if _normalize_worktree_submodules(worktree_submodules):
                if not await self._reset_nested_submodules_to_super_gitlinks(
                        cell,
                        worktree_submodules,
                ):
                    return False
            log.info("Reset '%s' to %s after merge",
                     cell.name, base)
            return True
        except Exception:
            log.exception("reset_to_base failed for '%s'", cell.name)
            return False

    async def _reset_nested_submodules_to_super_gitlinks(
            self,
            cell,
            worktree_submodules,
    ) -> bool:
        infos = await self._nested_submodule_infos_for_cell(
            cell,
            worktree_submodules,
            require_worktree=True,
            strict=False,
        )
        for info in infos:
            target = info.get("gitlink_sha", "")
            if not target:
                continue
            code, _out, err = await self._git_run(
                info["worktree_path"],
                "reset",
                "--hard",
                target,
            )
            if code != 0:
                log.warning(
                    "Nested submodule reset failed for '%s' %s: %s",
                    getattr(cell, "name", ""),
                    info.get("path", ""),
                    err,
                )
                return False
        return True

    async def rebase_onto_base(self, cell, worktree_submodules=None) -> bool:
        """Rebase the worktree branch onto its base branch.

        Returns True on success, False on failure (e.g. conflicts).
        On failure the rebase is aborted so the worktree is left clean.
        """
        if not cell.worktree_base_branch:
            return False
        wt_dir = self._isolated_worktree_dir(cell, "rebase_onto_base")
        if not wt_dir:
            return False
        try:
            submodule_paths = _normalize_worktree_submodules(worktree_submodules)
            if submodule_paths and not await self._rebase_nested_submodules(
                    cell,
                    submodule_paths,
            ):
                await self._reset_nested_submodules_to_super_gitlinks(
                    cell,
                    submodule_paths,
                )
                return False

            nested_updates: list[dict] = []
            if submodule_paths:
                nested_updates = [
                    {
                        "path": state.get("path", ""),
                        "sha": state.get("commit_sha", ""),
                    }
                    for state in await self.nested_submodule_head_states(
                        cell,
                        submodule_paths,
                    )
                ]

            rebase_result = await self._rebase_superproject_onto_base(
                wt_dir,
                cell.worktree_base_branch,
                nested_updates=nested_updates,
            )
            if not rebase_result.get("ok"):
                err = rebase_result.get("error", "")
                log.warning("Rebase failed for '%s': %s", cell.name, err)
                await self._abort_superproject_rebase_and_reset_nested(
                    wt_dir,
                    cell,
                    submodule_paths,
                )
                return False

            if nested_updates:
                bump = await self._commit_superproject_gitlink_bumps(
                    wt_dir,
                    nested_updates,
                    message="Update nested submodule gitlinks after rebase",
                )
                if not bump.get("ok"):
                    log.warning(
                        "Nested submodule gitlink bump after rebase failed "
                        "for '%s': %s",
                        cell.name,
                        bump.get("error", ""),
                    )
                    await self._reset_nested_submodules_to_super_gitlinks(
                        cell,
                        submodule_paths,
                    )
                    return False
            log.info("Rebased '%s' onto %s",
                     cell.name, cell.worktree_base_branch)
            return True
        except Exception:
            log.exception("Rebase failed for '%s'", cell.name)
            await self._abort_superproject_rebase_and_reset_nested(
                wt_dir,
                cell,
                _normalize_worktree_submodules(worktree_submodules),
            )
            return False

    async def _abort_superproject_rebase_and_reset_nested(
            self,
            wt_dir: str,
            cell,
            worktree_submodules,
    ) -> None:
        """Abort an in-progress superproject rebase and realign submodules."""
        try:
            await self._git_run(wt_dir, "rebase", "--abort")
        except Exception:
            log.debug("Superproject rebase abort failed for '%s'",
                      getattr(cell, "name", ""), exc_info=True)
        if _normalize_worktree_submodules(worktree_submodules):
            try:
                await self._reset_nested_submodules_to_super_gitlinks(
                    cell,
                    worktree_submodules,
                )
            except Exception:
                log.debug("Nested submodule rollback failed for '%s'",
                          getattr(cell, "name", ""), exc_info=True)

    async def _rebase_superproject_onto_base(
            self,
            wt_dir: str,
            base_branch: str,
            *,
            nested_updates: list[dict] | None = None,
    ) -> dict:
        """Rebase the superproject, resolving configured gitlink conflicts.

        Nested submodule branches are rebased before the superproject.  When
        the base superproject also advanced a gitlink, Git can stop while
        replaying the old gitlink checkpoint.  If the only unmerged paths are
        configured nested submodules, stage the already-rebased nested HEADs
        and continue so the rebased superproject records the reachable SHA.
        """
        updates = [
            {
                "path": _normalize_repo_rel_path(update.get("path", "")),
                "sha": update.get("sha", ""),
            }
            for update in (nested_updates or [])
            if update.get("path") and update.get("sha")
        ]
        code, _out, err = await self._git_run(wt_dir, "rebase", base_branch)
        if code == 0:
            return {"ok": True}
        if not updates:
            return {"ok": False, "error": err}

        last_error = err
        for _attempt in range(max(8, len(updates) * 4)):
            resolved = await self._resolve_nested_gitlink_rebase_conflicts(
                wt_dir,
                updates,
            )
            if not resolved.get("ok"):
                return {
                    "ok": False,
                    "error": last_error or resolved.get("error", ""),
                }

            code, out, err = await self._git_run_with_env(
                wt_dir,
                {
                    "GIT_EDITOR": "true",
                    "GIT_SEQUENCE_EDITOR": "true",
                },
                "rebase",
                "--continue",
            )
            if code == 0:
                return {"ok": True}
            last_error = err or out
            if self._git_rebase_continue_needs_skip(last_error):
                code, out, err = await self._git_run_with_env(
                    wt_dir,
                    {
                        "GIT_EDITOR": "true",
                        "GIT_SEQUENCE_EDITOR": "true",
                    },
                    "rebase",
                    "--skip",
                )
                if code == 0:
                    return {"ok": True}
                last_error = err or out

        return {
            "ok": False,
            "error": last_error or "git rebase did not converge",
        }

    async def _git_run_with_env(self, directory: str, env_updates: dict,
                                *args: str) -> tuple[int, str, str]:
        """Run git with additional environment variables."""
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in (env_updates or {}).items()})
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", directory, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await proc.communicate()
            return (
                proc.returncode,
                stdout.decode(errors="replace").strip(),
                stderr.decode(errors="replace").strip(),
            )
        except Exception as exc:
            log.debug("git command failed for %s: %s", directory, " ".join(args))
            return 1, "", str(exc)

    @staticmethod
    def _git_rebase_continue_needs_skip(message: str) -> bool:
        text = (message or "").lower()
        return (
            "previous cherry-pick is now empty" in text
            or "no changes - did you forget to use 'git add'" in text
            or "nothing to commit" in text and "rebase --skip" in text
        )

    async def _resolve_nested_gitlink_rebase_conflicts(
            self,
            wt_dir: str,
            updates: list[dict],
    ) -> dict:
        allowed = {
            _normalize_repo_rel_path(update.get("path", "")): update.get("sha", "")
            for update in updates
            if update.get("path") and update.get("sha")
        }
        code, out = await self._git_stdout(
            wt_dir,
            "diff",
            "--name-only",
            "--diff-filter=U",
        )
        if code != 0:
            return {"ok": False, "error": "Could not inspect rebase conflicts"}
        unmerged = [
            _normalize_repo_rel_path(line.strip())
            for line in out.splitlines()
            if line.strip()
        ]
        if not unmerged:
            return {"ok": False, "error": "Rebase failed without conflicts"}
        unexpected = [path for path in unmerged if path not in allowed]
        if unexpected:
            return {
                "ok": False,
                "error": "Rebase has non-gitlink conflicts: "
                         + ", ".join(unexpected),
            }

        for path in unmerged:
            sha = allowed.get(path, "")
            sub_wt = self._join_repo_rel(wt_dir, path)
            code, _out, err = await self._git_run(
                sub_wt,
                "reset",
                "--hard",
                sha,
            )
            if code != 0:
                return {
                    "ok": False,
                    "error": (
                        f"Could not reset nested submodule {path} to "
                        f"{sha[:12]} while resolving rebase: {err}"
                    ),
                }
            code, _out, err = await self._git_run(wt_dir, "add", "--", path)
            if code != 0:
                return {
                    "ok": False,
                    "error": (
                        f"Could not stage nested submodule {path} while "
                        f"resolving rebase: {err}"
                    ),
                }

        code, out = await self._git_stdout(
            wt_dir,
            "diff",
            "--name-only",
            "--diff-filter=U",
        )
        if code != 0 or out.strip():
            return {
                "ok": False,
                "error": "Nested gitlink conflicts were not fully resolved",
            }
        return {"ok": True, "paths": unmerged}

    async def _rebase_nested_submodules(self, cell,
                                        worktree_submodules) -> bool:
        base = str(getattr(cell, "worktree_base_branch", "") or "").strip()
        if not base:
            return True
        infos = await self._nested_submodule_infos_for_cell(
            cell,
            worktree_submodules,
            require_worktree=True,
            strict=False,
        )
        for info in infos:
            sub_wt = info["worktree_path"]
            code, status = await self._git_stdout(
                sub_wt,
                "status",
                "--porcelain",
            )
            if code != 0 or status.strip():
                log.warning(
                    "Nested submodule rebase refused for '%s' %s: dirty",
                    getattr(cell, "name", ""),
                    info.get("path", ""),
                )
                return False
            code, _out, err = await self._git_run(sub_wt, "rebase", base)
            if code != 0:
                log.warning(
                    "Nested submodule rebase failed for '%s' %s: %s",
                    getattr(cell, "name", ""),
                    info.get("path", ""),
                    err,
                )
                await self._git_run(sub_wt, "rebase", "--abort")
                return False
        return True

    async def count_commits(self, cell) -> int:
        """Count commits on the worktree branch since the fork point."""
        if not cell.worktree_path or not cell.worktree_base_branch:
            return 0
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
                "rev-list", "--count",
                f"{cell.worktree_base_branch}..HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return int(stdout.decode().strip())
        except Exception:
            log.debug("count_commits failed for '%s'", cell.name)
        return 0

    async def count_behind(self, cell) -> int:
        """Count commits on base branch not reachable from the worktree."""
        if not cell.worktree_path or not cell.worktree_base_branch:
            return 0
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
                "rev-list", "--count",
                f"HEAD..{cell.worktree_base_branch}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return int(stdout.decode().strip())
        except Exception:
            log.debug("count_behind failed for '%s'", cell.name)
        return 0

    async def _run_capture(self, *cmd: str,
                           cwd: str | None = None) -> dict:
        """Run a subprocess and capture stdout/stderr as text."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or None,
            )
            stdout, stderr = await proc.communicate()
            return {
                "returncode": proc.returncode,
                "stdout": _decode_process_output(stdout),
                "stderr": _decode_process_output(stderr),
                "cmd": list(cmd),
                "cwd": cwd or "",
            }
        except FileNotFoundError as exc:
            return {
                "returncode": 127,
                "stdout": "",
                "stderr": str(exc),
                "cmd": list(cmd),
                "cwd": cwd or "",
                "missing": True,
            }
        except Exception as exc:
            log.debug("command failed: %s", " ".join(cmd), exc_info=True)
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": str(exc),
                "cmd": list(cmd),
                "cwd": cwd or "",
            }

    async def _run_gh(self, worktree_path: str, *args: str) -> dict:
        """Run ``gh`` with *worktree_path* as cwd.

        GitHub CLI does not provide git's ``-C`` flag on all supported
        versions. Running with ``cwd`` preserves the intended repo context
        without depending on an unsupported global flag.
        """
        return await self._run_capture("gh", *args, cwd=worktree_path)

    async def github_preflight(self, worktree_path: str) -> dict:
        """Verify GitHub CLI availability, auth, and repo context."""
        phase = "github_preflight"
        if not worktree_path:
            return _worktree_error(phase, "No worktree path provided.")

        version = await self._run_capture("gh", "--version")
        if version.get("returncode") != 0:
            return _worktree_error(
                phase,
                "GitHub CLI (gh) is not installed or not executable.",
            )

        # Scope the auth check to the TARGET remote's host so an unrelated,
        # unreachable gh account on a different host (e.g. an enterprise host
        # whose keyring login times out) cannot block a merge to github.com.
        # Resolve the host from the worktree's preferred GitHub remote; fall
        # back to a host-agnostic check only when no GitHub host is found.
        target_host = ""
        remotes = await self._run_capture(
            "git", "-C", worktree_path, "remote", "-v"
        )
        if remotes.get("returncode") == 0:
            _remote, remote_url = _select_github_remote_from_remote_v(
                remotes.get("stdout") or ""
            )
            target_host = _github_host_from_url(remote_url)

        auth_args = ["auth", "status"]
        if target_host:
            auth_args += ["--hostname", target_host]
        auth = await self._run_gh(worktree_path, *auth_args)
        if auth.get("returncode") != 0:
            err = auth.get("stderr") or auth.get("stdout") \
                or "gh auth status failed"
            scope = f" for {target_host}" if target_host else ""
            return _worktree_error(
                phase,
                f"GitHub CLI authentication failed{scope}: {err}",
            )

        repo = await self._run_gh(
            worktree_path,
            "repo",
            "view",
            "--json",
            "nameWithOwner,url",
        )
        if repo.get("returncode") != 0:
            err = repo.get("stderr") or repo.get("stdout") \
                or "gh repo view failed"
            return _worktree_error(
                phase,
                f"Not a GitHub repository or cannot inspect repo: {err}",
            )

        try:
            data = json.loads(repo.get("stdout") or "{}")
        except json.JSONDecodeError:
            return _worktree_error(
                phase,
                "gh repo view returned invalid JSON.",
            )
        return _worktree_ok(
            phase,
            name_with_owner=str(data.get("nameWithOwner") or "").strip(),
            url=str(data.get("url") or "").strip(),
        )

    async def github_select_remote(self, worktree_path: str) -> dict:
        """Select the GitHub remote to use for PR push/merge operations."""
        phase = "github_remote"
        if not worktree_path:
            return _worktree_error(phase, "No worktree path provided.")
        remotes = await self._run_capture(
            "git", "-C", worktree_path, "remote", "-v"
        )
        if remotes.get("returncode") != 0:
            err = remotes.get("stderr") or remotes.get("stdout") \
                or "git remote -v failed"
            return _worktree_error(phase, f"Failed to inspect remotes: {err}")

        remote, url = _select_github_remote_from_remote_v(
            remotes.get("stdout") or ""
        )
        if not remote:
            return _worktree_error(
                phase,
                "PR-based merge requires a GitHub remote; none found.",
            )
        return _worktree_ok(
            phase,
            remote=remote,
            url=url,
        )

    async def github_sync_remote_base(self, worktree_path: str, repo_root: str,
                                      remote: str,
                                      base_branch: str) -> dict:
        """Fetch and fast-forward the local base branch to remote base."""
        phase = "remote_base_sync"
        worktree_path = str(worktree_path or "").strip()
        repo_root = str(repo_root or "").strip() or worktree_path
        remote = str(remote or "").strip()
        base_branch = str(base_branch or "").strip()
        if not repo_root or not remote or not base_branch:
            return _worktree_error(
                phase,
                "Repo root, remote, and base branch are required.",
            )

        remote_ref = f"refs/remotes/{remote}/{base_branch}"
        fetch_refspec = f"+refs/heads/{base_branch}:{remote_ref}"
        fetch = await self._run_capture(
            "git", "-C", repo_root,
            "fetch", "--prune", remote, fetch_refspec,
        )
        if fetch.get("returncode") != 0:
            err = fetch.get("stderr") or fetch.get("stdout") \
                or "git fetch failed"
            return _worktree_error(
                phase,
                f"Failed to fetch {remote}/{base_branch}: {err}",
                remote=remote,
                base_branch=base_branch,
            )

        base = await self._run_capture(
            "git", "-C", repo_root, "rev-parse", base_branch
        )
        if base.get("returncode") != 0 or not base.get("stdout"):
            err = base.get("stderr") or base.get("stdout") \
                or f"Cannot resolve {base_branch}"
            return _worktree_error(phase, err, remote=remote,
                                   base_branch=base_branch)
        base_sha = base.get("stdout", "").splitlines()[0].strip()

        remote_head = await self._run_capture(
            "git", "-C", repo_root, "rev-parse", remote_ref
        )
        if remote_head.get("returncode") != 0 or not remote_head.get("stdout"):
            err = remote_head.get("stderr") or remote_head.get("stdout") \
                or f"Cannot resolve {remote_ref}"
            return _worktree_error(phase, err, remote=remote,
                                   base_branch=base_branch)
        remote_sha = remote_head.get("stdout", "").splitlines()[0].strip()

        if base_sha == remote_sha:
            return _worktree_ok(
                phase,
                remote=remote,
                base_branch=base_branch,
                base_sha=base_sha,
                remote_sha=remote_sha,
                synced=False,
            )

        ff = await self._run_capture(
            "git", "-C", repo_root,
            "merge-base", "--is-ancestor", base_branch, remote_ref,
        )
        if ff.get("returncode") != 0:
            return _worktree_error(
                phase,
                f"Local {base_branch} cannot be fast-forwarded to "
                f"{remote}/{base_branch}; resolve divergence before PR merge.",
                remote=remote,
                base_branch=base_branch,
                base_sha=base_sha,
                remote_sha=remote_sha,
            )

        current = await self._run_capture(
            "git", "-C", repo_root, "symbolic-ref", "--short", "HEAD"
        )
        checked_out = current.get("stdout", "") \
            if current.get("returncode") == 0 else ""
        if checked_out == base_branch:
            sync = await self._run_capture(
                "git", "-C", repo_root,
                "merge", "--ff-only", remote_ref,
            )
        else:
            sync = await self._run_capture(
                "git", "-C", repo_root,
                "update-ref", f"refs/heads/{base_branch}",
                remote_sha, base_sha,
            )
        if sync.get("returncode") != 0:
            err = sync.get("stderr") or sync.get("stdout") \
                or "base sync failed"
            return _worktree_error(
                phase,
                err,
                remote=remote,
                base_branch=base_branch,
                base_sha=base_sha,
                remote_sha=remote_sha,
            )

        return _worktree_ok(
            phase,
            remote=remote,
            base_branch=base_branch,
            base_sha=remote_sha,
            previous_base_sha=base_sha,
            remote_sha=remote_sha,
            synced=True,
        )

    async def github_remote_branch_sha(self, repo_root: str, remote: str,
                                       branch: str) -> dict:
        """Read the remote branch SHA without updating local refs."""
        phase = "remote_base_ground_truth"
        repo_root = str(repo_root or "").strip()
        remote = str(remote or "").strip()
        branch = str(branch or "").strip()
        if not repo_root or not remote or not branch:
            return _worktree_error(
                phase,
                "Repo root, remote, and branch are required.",
            )

        ref = f"refs/heads/{branch}"
        result = await self._run_capture(
            "git", "-C", repo_root, "ls-remote", remote, ref,
        )
        if result.get("returncode") != 0:
            err = result.get("stderr") or result.get("stdout") \
                or "git ls-remote failed"
            return _worktree_error(
                phase,
                f"Failed to inspect {remote}/{branch}: {err}",
                remote=remote,
                base_branch=branch,
            )
        stdout = str(result.get("stdout") or "").strip()
        parts = stdout.split()
        sha = parts[0].strip() if parts else ""
        if not sha:
            return _worktree_error(
                phase,
                f"Remote branch {remote}/{branch} was not found.",
                remote=remote,
                base_branch=branch,
            )
        return _worktree_ok(
            phase,
            remote=remote,
            base_branch=branch,
            sha=sha,
            remote_sha=sha,
        )

    async def github_push_branch(self, worktree_path: str, remote: str,
                                 branch: str) -> dict:
        """Push a worktree branch to the selected remote."""
        phase = "push_branch"
        if not worktree_path or not remote or not branch:
            return _worktree_error(
                phase,
                "Worktree path, remote, and branch are required.",
            )
        push = await self._run_capture(
            "git", "-C", worktree_path,
            "push", "-u", remote, branch,
        )
        if push.get("returncode") != 0:
            err = push.get("stderr") or push.get("stdout") \
                or "git push failed"
            return _worktree_error(
                phase,
                f"Failed to push branch: {err}",
                remote=remote,
                branch=branch,
            )
        return _worktree_ok(phase, remote=remote, branch=branch)

    async def _github_push_branch_force_safety(
        self,
        worktree_path: str,
        remote: str,
        branch: str,
        base_branch: str,
    ) -> dict:
        """Validate whether a non-FF branch push can be retried with a lease."""
        phase = "push_branch_safety"
        worktree_path = str(worktree_path or "").strip()
        remote = str(remote or "").strip()
        branch = str(branch or "").strip()
        base_branch = str(base_branch or "").strip()
        if not worktree_path or not remote or not branch or not base_branch:
            return _worktree_error(
                phase,
                "Worktree path, remote, branch, and base branch are required.",
                safe=False,
            )

        status = await self._run_capture(
            "git", "-C", worktree_path,
            "status", "--porcelain",
        )
        if status.get("returncode") != 0:
            err = status.get("stderr") or status.get("stdout") \
                or "git status failed"
            return _worktree_error(
                phase,
                f"Cannot verify clean worktree before force-push: {err}",
                safe=False,
            )
        if str(status.get("stdout") or "").strip():
            return _worktree_error(
                phase,
                "Worktree has uncommitted changes; refusing auto force-push.",
                safe=False,
            )

        remote_ref = f"refs/remotes/{remote}/{branch}"
        base_ref = f"refs/remotes/{remote}/{base_branch}"
        fetch_branch = await self._run_capture(
            "git", "-C", worktree_path,
            "fetch", "--no-tags", remote,
            f"+refs/heads/{branch}:{remote_ref}",
        )
        if fetch_branch.get("returncode") != 0:
            err = fetch_branch.get("stderr") or fetch_branch.get("stdout") \
                or "git fetch failed"
            return _worktree_error(
                phase,
                f"Cannot fetch remote branch {remote}/{branch}: {err}",
                safe=False,
            )
        fetch_base = await self._run_capture(
            "git", "-C", worktree_path,
            "fetch", "--no-tags", remote,
            f"+refs/heads/{base_branch}:{base_ref}",
        )
        if fetch_base.get("returncode") != 0:
            err = fetch_base.get("stderr") or fetch_base.get("stdout") \
                or "git fetch failed"
            return _worktree_error(
                phase,
                f"Cannot fetch remote base {remote}/{base_branch}: {err}",
                safe=False,
            )

        async def rev(ref: str) -> str:
            resolved = await self._run_capture(
                "git", "-C", worktree_path,
                "rev-parse", "--verify", f"{ref}^{{commit}}",
            )
            if resolved.get("returncode") != 0:
                return ""
            return str(resolved.get("stdout") or "").splitlines()[0].strip()

        remote_sha = await rev(remote_ref)
        local_sha = await rev(branch)
        base_sha = await rev(base_ref)
        if not remote_sha or not local_sha or not base_sha:
            return _worktree_error(
                phase,
                "Cannot resolve local branch, remote branch, or remote base.",
                safe=False,
                remote_ref=remote_ref,
                base_ref=base_ref,
                remote_sha=remote_sha,
                local_sha=local_sha,
                base_sha=base_sha,
            )

        base_in_local = await self._run_capture(
            "git", "-C", worktree_path,
            "merge-base", "--is-ancestor", base_sha, local_sha,
        )
        local_includes_base = base_in_local.get("returncode") == 0
        if not local_includes_base:
            return _worktree_error(
                phase,
                "Local branch does not include the current remote base tip; "
                "refusing auto force-push.",
                safe=False,
                remote_ref=remote_ref,
                base_ref=base_ref,
                remote_sha=remote_sha,
                local_sha=local_sha,
                base_sha=base_sha,
                local_includes_base=False,
            )

        remote_in_local = await self._run_capture(
            "git", "-C", worktree_path,
            "merge-base", "--is-ancestor", remote_sha, local_sha,
        )
        remote_ancestor_of_local = remote_in_local.get("returncode") == 0
        remote_merged_to_base = False
        reason = ""
        if remote_ancestor_of_local:
            reason = "remote_ancestor_of_local"
        else:
            remote_merged_to_base = await self.is_branch_merged(
                worktree_path,
                branch=remote_ref,
                base_branch=base_ref,
            )
            if remote_merged_to_base:
                reason = "remote_merged_to_base"

        if not reason:
            return _worktree_error(
                phase,
                "Remote branch is neither an ancestor of the local branch nor "
                "already incorporated into the current remote base; refusing "
                "auto force-push.",
                safe=False,
                remote_ref=remote_ref,
                base_ref=base_ref,
                remote_sha=remote_sha,
                local_sha=local_sha,
                base_sha=base_sha,
                local_includes_base=True,
                remote_ancestor_of_local=False,
                remote_merged_to_base=False,
            )

        return _worktree_ok(
            phase,
            safe=True,
            reason=reason,
            remote=remote,
            branch=branch,
            base_branch=base_branch,
            remote_ref=remote_ref,
            base_ref=base_ref,
            remote_sha=remote_sha,
            local_sha=local_sha,
            base_sha=base_sha,
            local_includes_base=True,
            remote_ancestor_of_local=remote_ancestor_of_local,
            remote_merged_to_base=remote_merged_to_base,
        )

    async def github_force_push_branch_with_lease_if_safe(
        self,
        worktree_path: str,
        remote: str,
        branch: str,
        *,
        base_branch: str,
        push_error: dict | None = None,
    ) -> dict:
        """Retry a rejected PR branch push using --force-with-lease if safe."""
        phase = "push_branch"
        initial_error = _push_result_text(push_error)
        if not _is_non_fast_forward_push_error(initial_error):
            return _worktree_error(
                phase,
                "Auto force-with-lease not attempted: initial push failure "
                "was not a non-fast-forward rejection.",
                non_fast_forward=False,
                auto_force_push=False,
                safety_gate_passed=False,
            )

        safety = await self._github_push_branch_force_safety(
            worktree_path,
            remote,
            branch,
            base_branch,
        )
        if not safety.get("ok"):
            return _worktree_error(
                phase,
                safety.get("error", "Auto force-with-lease safety gate failed."),
                non_fast_forward=True,
                auto_force_push=False,
                safety_gate_passed=False,
                auto_force_safety=safety,
                remote=remote,
                branch=branch,
                base_branch=base_branch,
            )

        lease_ref = f"refs/heads/{branch}"
        lease_sha = str(safety.get("remote_sha") or "").strip()
        force_push = await self._run_capture(
            "git", "-C", worktree_path,
            "push",
            f"--force-with-lease={lease_ref}:{lease_sha}",
            "-u", remote, branch,
        )
        if force_push.get("returncode") != 0:
            err = force_push.get("stderr") or force_push.get("stdout") \
                or "git push --force-with-lease failed"
            return _worktree_error(
                phase,
                f"Failed to force-push branch with lease: {err}",
                non_fast_forward=True,
                auto_force_push=False,
                force_with_lease=True,
                safety_gate_passed=True,
                auto_force_safety=safety,
                remote=remote,
                branch=branch,
                base_branch=base_branch,
            )

        return _worktree_ok(
            phase,
            remote=remote,
            branch=branch,
            base_branch=base_branch,
            non_fast_forward=True,
            auto_force_push=True,
            force_with_lease=True,
            safety_gate_passed=True,
            auto_force_reason=safety.get("reason", ""),
            force_lease_ref=lease_ref,
            force_lease_sha=lease_sha,
            remote_sha=lease_sha,
            local_sha=safety.get("local_sha", ""),
            base_sha=safety.get("base_sha", ""),
            auto_force_safety=safety,
            initial_push_error=initial_error,
        )

    async def github_pr_view(self, worktree_path: str,
                             selector: str | int) -> dict:
        """Return structured GitHub PR status from ``gh pr view``."""
        phase = "pr_view"
        selector_text = str(selector or "").strip()
        if not worktree_path or not selector_text:
            return _worktree_error(
                phase,
                "Worktree path and PR selector are required.",
            )
        view = await self._run_gh(
            worktree_path,
            "pr",
            "view",
            selector_text,
            "--json",
            _GITHUB_PR_VIEW_FIELDS,
        )
        if view.get("returncode") != 0:
            err = view.get("stderr") or view.get("stdout") \
                or "gh pr view failed"
            return _worktree_error(phase, err)
        try:
            data = json.loads(view.get("stdout") or "{}")
        except json.JSONDecodeError:
            return _worktree_error(phase, "gh pr view returned invalid JSON.")
        return _pr_result_from_view_data(data, phase=phase)

    async def github_pr_status(self, worktree_path: str,
                               selector: str | int) -> dict:
        """Alias for callers that need an explicit PR status helper."""
        return await self.github_pr_view(worktree_path, selector)

    async def github_pr_edit_body(self, worktree_path: str,
                                  selector: str | int,
                                  body: str = "") -> dict:
        """Update an existing GitHub PR body via ``gh pr edit --body``."""
        phase = "pr_edit_body"
        selector_text = str(selector or "").strip()
        if not worktree_path or not selector_text:
            return _worktree_error(
                phase,
                "Worktree path and PR selector are required.",
            )

        edit = await self._run_gh(
            worktree_path,
            "pr",
            "edit",
            selector_text,
            "--body",
            body or "",
        )
        if edit.get("returncode") != 0:
            err = edit.get("stderr") or edit.get("stdout") \
                or "gh pr edit failed"
            return _worktree_error(
                phase,
                f"Failed to update PR body: {err}",
            )

        status = await self.github_pr_view(worktree_path, selector_text)
        if status.get("ok"):
            status.update({"phase": phase, "body": body or ""})
            return status
        return _worktree_ok(phase, selector=selector_text, body=body or "")

    async def github_create_or_reuse_pr(self, worktree_path: str, branch: str,
                                        base_branch: str, title: str = "",
                                        body: str = "") -> dict:
        """Create a PR for *branch*, or reuse an existing open PR."""
        phase = "pr_create"
        if not worktree_path or not branch:
            return _worktree_error(
                phase,
                "Worktree path and branch are required.",
            )
        base_branch = base_branch or "main"

        existing = await self.github_pr_view(worktree_path, branch)
        if existing.get("ok"):
            existing_state = str(existing.get("state") or "").upper()
            if existing_state == "OPEN":
                existing.update({"phase": phase, "existing": True})
                return existing
            # Reused Torque worker branches can have older merged PRs while
            # also carrying fresh follow-up commits.  Do not treat a stale
            # non-open PR lookup as proof the current branch is already
            # landed; only the explicit create-time "No commits between"
            # response below is safe to convert into already_merged.

        create = await self._run_gh(
            worktree_path,
            "pr",
            "create",
            "--base",
            base_branch,
            "--head",
            branch,
            "--title",
            title or branch,
            "--body",
            body or "",
        )
        if create.get("returncode") != 0:
            err = create.get("stderr") or create.get("stdout") \
                or "gh pr create failed"
            lowered = err.lower()
            if "already exists" in lowered:
                reused = await self.github_pr_view(worktree_path, branch)
                if reused.get("ok"):
                    reused.update({"phase": phase, "existing": True})
                    return reused
            if _is_no_commits_between_pr_create_error(
                    err,
                    base_branch=base_branch,
                    branch=branch,
            ):
                reused = await self.github_pr_view(worktree_path, branch)
                warning = (
                    f"GitHub reported no commits between {base_branch} "
                    f"and {branch}; treating PR creation as already landed."
                )
                if reused.get("ok"):
                    reused.update({
                        "phase": phase,
                        "existing": True,
                        "already_merged": True,
                        "no_commits_between": True,
                        "warning": warning,
                        "pr_create_error": err,
                    })
                    return reused
                return _worktree_ok(
                    phase,
                    url="",
                    number=None,
                    head_sha="",
                    state="MERGED",
                    merge_state="",
                    merge_commit_sha="",
                    existing=False,
                    already_merged=True,
                    no_commits_between=True,
                    warning=warning,
                    pr_create_error=err,
                    base_branch=base_branch,
                    branch=branch,
                )
            return _worktree_error(phase, f"Failed to create PR: {err}")

        url = (create.get("stdout") or "").splitlines()[-1].strip()
        created = await self.github_pr_view(worktree_path, branch)
        if created.get("ok"):
            created.update({"phase": phase, "existing": False})
            if url and not created.get("url"):
                created["url"] = url
            return created

        number = _extract_pr_number_from_url(url)
        return _worktree_ok(
            phase,
            url=url,
            number=number,
            head_sha="",
            existing=False,
        )

    async def github_request_squash_merge(self, worktree_path: str,
                                          pr_number: int | str,
                                          head_sha: str,
                                          subject: str = "",
                                          body: str = "",
                                          auto: bool = False,
                                          url: str = "") -> dict:
        """Request a GitHub squash merge guarded by the expected head SHA."""
        phase = "pr_merge"
        pr_selector = str(pr_number or "").strip()
        head_sha = str(head_sha or "").strip()
        if not worktree_path or not pr_selector or not head_sha:
            return _worktree_error(
                phase,
                "Worktree path, PR number, and head SHA are required.",
                url=url,
                number=pr_number,
                head_sha=head_sha,
                pending=False,
            )

        cmd = [
            "pr",
            "merge",
            pr_selector,
            "--squash",
            "--match-head-commit",
            head_sha,
        ]
        if subject:
            cmd.extend(["--subject", subject])
        cmd.extend(["--body", body or ""])
        if auto:
            cmd.append("--auto")

        merge = await self._run_gh(worktree_path, *cmd)
        if merge.get("returncode") != 0:
            err = merge.get("stderr") or merge.get("stdout") \
                or "gh pr merge failed"
            status = await self.github_pr_view(worktree_path, pr_selector)
            result = _worktree_error(
                phase,
                f"Failed to squash-merge PR: {err}",
                url=url or status.get("url", ""),
                number=status.get("number", pr_number),
                head_sha=head_sha,
                merge_commit_sha=status.get("merge_commit_sha", ""),
                merge_state=status.get("merge_state", ""),
                pending=False,
            )
            if status.get("ok"):
                result["pr_status"] = status
            return result

        status = await self.github_pr_view(worktree_path, pr_selector)
        if not status.get("ok"):
            return _worktree_error(
                phase,
                "Squash merge command succeeded, but PR status could not be "
                f"verified: {status.get('error', 'unknown error')}",
                url=url,
                number=pr_number,
                head_sha=head_sha,
                pending=False,
            )

        merged = bool(status.get("merged_at")) \
            or status.get("state") == "MERGED" \
            or bool(status.get("merge_commit_sha"))
        if merged:
            return _worktree_ok(
                phase,
                url=status.get("url") or url,
                number=status.get("number", pr_number),
                head_sha=head_sha,
                merge_commit_sha=status.get("merge_commit_sha", ""),
                merge_state=status.get("merge_state", ""),
                pending=False,
                pr_status=status,
            )

        if auto:
            return _worktree_ok(
                phase,
                url=status.get("url") or url,
                number=status.get("number", pr_number),
                head_sha=head_sha,
                merge_commit_sha="",
                merge_state=status.get("merge_state", ""),
                pending=True,
                pr_status=status,
            )

        return _worktree_error(
            phase,
            "Squash merge command completed but the PR is not merged.",
            url=status.get("url") or url,
            number=status.get("number", pr_number),
            head_sha=head_sha,
            merge_commit_sha=status.get("merge_commit_sha", ""),
            merge_state=status.get("merge_state", ""),
            pending=False,
            pr_status=status,
        )

    async def github_request_merge_commit_merge(
            self,
            worktree_path: str,
            pr_number: int | str,
            head_sha: str,
            subject: str = "",
            body: str = "",
            auto: bool = False,
            url: str = "",
            phase: str = "pr_merge",
    ) -> dict:
        """Request a GitHub merge-commit merge guarded by expected head SHA."""
        pr_selector = str(pr_number or "").strip()
        head_sha = str(head_sha or "").strip()
        if not worktree_path or not pr_selector or not head_sha:
            return _worktree_error(
                phase,
                "Worktree path, PR number, and head SHA are required.",
                url=url,
                number=pr_number,
                head_sha=head_sha,
                pending=False,
            )

        cmd = [
            "pr",
            "merge",
            pr_selector,
            "--merge",
            "--match-head-commit",
            head_sha,
        ]
        if subject:
            cmd.extend(["--subject", subject])
        cmd.extend(["--body", body or ""])
        if auto:
            cmd.append("--auto")

        merge = await self._run_gh(worktree_path, *cmd)
        if merge.get("returncode") != 0:
            err = merge.get("stderr") or merge.get("stdout") \
                or "gh pr merge failed"
            status = await self.github_pr_view(worktree_path, pr_selector)
            result = _worktree_error(
                phase,
                f"Failed to merge-commit PR: {err}",
                url=url or status.get("url", ""),
                number=status.get("number", pr_number),
                head_sha=head_sha,
                merge_commit_sha=status.get("merge_commit_sha", ""),
                merge_state=status.get("merge_state", ""),
                pending=False,
            )
            if status.get("ok"):
                result["pr_status"] = status
            return result

        status = await self.github_pr_view(worktree_path, pr_selector)
        if not status.get("ok"):
            return _worktree_error(
                phase,
                "Merge-commit command succeeded, but PR status could not be "
                f"verified: {status.get('error', 'unknown error')}",
                url=url,
                number=pr_number,
                head_sha=head_sha,
                pending=False,
            )

        merged = bool(status.get("merged_at")) \
            or status.get("state") == "MERGED" \
            or bool(status.get("merge_commit_sha"))
        if merged:
            return _worktree_ok(
                phase,
                url=status.get("url") or url,
                number=status.get("number", pr_number),
                head_sha=head_sha,
                merge_commit_sha=status.get("merge_commit_sha", ""),
                merge_state=status.get("merge_state", ""),
                pending=False,
                pr_status=status,
            )

        if auto:
            return _worktree_ok(
                phase,
                url=status.get("url") or url,
                number=status.get("number", pr_number),
                head_sha=head_sha,
                merge_commit_sha="",
                merge_state=status.get("merge_state", ""),
                pending=True,
                pr_status=status,
            )

        return _worktree_error(
            phase,
            "Merge-commit command completed but the PR is not merged.",
            url=status.get("url") or url,
            number=status.get("number", pr_number),
            head_sha=head_sha,
            merge_commit_sha=status.get("merge_commit_sha", ""),
            merge_state=status.get("merge_state", ""),
            pending=False,
            pr_status=status,
        )

    async def github_delete_remote_branch(self, worktree_path: str,
                                          remote: str,
                                          branch: str) -> dict:
        """Delete a remote branch after a confirmed successful PR merge."""
        phase = "remote_branch_delete"
        if not worktree_path or not remote or not branch:
            return _worktree_error(
                phase,
                "Worktree path, remote, and branch are required.",
            )
        delete = await self._run_capture(
            "git", "-C", worktree_path, "push", remote, "--delete", branch
        )
        if delete.get("returncode") != 0:
            err = delete.get("stderr") or delete.get("stdout") \
                or "remote branch delete failed"
            lowered = err.lower()
            if "remote ref does not exist" in lowered \
                    or "not found" in lowered:
                return _worktree_ok(
                    phase,
                    remote=remote,
                    branch=branch,
                    deleted=False,
                )
            return _worktree_error(
                phase,
                f"Failed to delete remote branch: {err}",
                remote=remote,
                branch=branch,
            )
        return _worktree_ok(
            phase,
            remote=remote,
            branch=branch,
            deleted=True,
        )

    async def create_pr(self, cell, title: str = "",
                        body: str = "",
                        worktree_submodules=None) -> dict:
        """Push the worktree branch and create a GitHub PR.

        Returns structured GitHub PR metadata on success or an ``error`` on
        failure.  This compatibility wrapper keeps the existing create-PR
        command path while exposing smaller primitives for the PR-based merge
        flow.
        """
        if not cell.worktree_path or not cell.worktree_branch:
            return _worktree_error(
                "create_pr",
                "No worktree branch found for this agent.",
            )

        wt = cell.worktree_path
        branch = cell.worktree_branch
        base = cell.worktree_base_branch or "main"

        preflight = await self.github_preflight(wt)
        if not preflight.get("ok"):
            return preflight

        remote_info = await self.github_select_remote(wt)
        if not remote_info.get("ok"):
            return remote_info

        # Check branch has commits ahead of base
        count = await self.count_commits(cell)
        if count == 0:
            return _worktree_error(
                "create_pr",
                f"Branch {branch} has no commits ahead of {base}.",
            )

        submodule_paths = _normalize_worktree_submodules(worktree_submodules)
        ee_pr_submodule_paths = _ee_pr_flow_submodule_paths(submodule_paths)
        legacy_submodule_paths = _legacy_nested_submodule_paths(
            submodule_paths,
            ee_pr_submodule_paths,
        )
        if ee_pr_submodule_paths:
            nested_pr = await self.merge_nested_submodules_via_pr_for_merge(
                cell,
                ee_pr_submodule_paths,
                title=title,
                body=body,
                merge=False,
            )
            if not nested_pr.get("ok"):
                return nested_pr
            if nested_pr.get("pending"):
                first_pr = {}
                for sub in nested_pr.get("submodules", []) or []:
                    pr = sub.get("pr") if isinstance(sub, dict) else {}
                    if isinstance(pr, dict) and pr.get("url"):
                        first_pr = pr
                        break
                return {
                    **nested_pr,
                    "phase": "nested_submodule_pr_create",
                    "url": first_pr.get("url", ""),
                    "existing": bool(first_pr.get("existing")),
                    "pending_ee_pr": True,
                    "message": (
                        "Nested submodule PR created/reused; parent PR will be "
                        "created after the nested PR merges."
                    ),
                }
        if legacy_submodule_paths:
            published = await self.publish_nested_submodule_branches_for_merge(
                cell,
                legacy_submodule_paths,
            )
            if not published.get("ok"):
                return published
            nested_preflight = await self.nested_submodule_merge_preflight(
                cell,
                legacy_submodule_paths,
            )
            if not nested_preflight.get("ok"):
                return nested_preflight

        pushed = await self.github_push_branch(
            wt, remote_info.get("remote", "origin"), branch
        )
        if not pushed.get("ok"):
            return pushed

        pr = await self.github_create_or_reuse_pr(
            wt, branch, base, title=title, body=body
        )
        if not pr.get("ok"):
            return pr

        log.info("%s PR for '%s': %s",
                 "Reused" if pr.get("existing") else "Created",
                 cell.name, pr.get("url", ""))
        return pr

    async def _ensure_gitignore(self, repo_root: str):
        """Add .torque/worktrees/ to .gitignore if not already present.

        Only the worktree directory belongs in .gitignore (shared across
        clones).  All other Torque-injected files are excluded via
        .git/info/exclude (per-checkout, not version-controlled).
        """
        gitignore = os.path.join(repo_root, ".gitignore")
        entry = ".torque/worktrees/"
        try:
            content = ""
            if os.path.exists(gitignore):
                with open(gitignore) as f:
                    content = f.read()
            if entry not in content.splitlines():
                with open(gitignore, "a") as f:
                    if content and not content.endswith("\n"):
                        f.write("\n")
                    f.write(f"{entry}\n")
                log.info("Added %s to .gitignore in %s", entry, repo_root)
            # Exclude Torque-injected files via .git/info/exclude
            ensure_git_exclude(repo_root)
            # Install the fail-closed worktree-isolation guard hook so a
            # worker can never commit into the shared main checkout (TORQUE:580).
            ensure_worktree_isolation_guard(repo_root)
        except Exception:
            log.debug("Could not update .gitignore in %s", repo_root)
