"""Git worktree lifecycle management for Torque agents."""

import asyncio
import glob
import json
import os
import re
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
_WORKTREE_NAME_MAX_LEN = 40
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

    def __init__(self):
        # Per-cell ephemeral fingerprint of (worktree_index_mtime,
        # base_ref_mtime). Used by `refresh_state` to skip the entire
        # status/diff/ahead-behind/is_merged probe when neither side has
        # advanced since the last tick.
        self._refresh_fingerprints: dict[str, tuple[float, float]] = {}

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

    def _refresh_fingerprint(self, cell) -> tuple[float, float]:
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
        return (index_mtime, base_mtime)

    async def refresh_state(self, cell) -> bool:
        """Refresh worktree-derived ephemeral fields on ``cell`` in one pass.

        Returns True if any field changed. Skips the work entirely when
        the cheap mtime fingerprint matches the last successful refresh,
        which is the common case (most agents are idle most ticks).
        """
        if not cell.worktree_path or not cell.worktree_base_branch:
            return False

        fingerprint = self._refresh_fingerprint(cell)
        previous = self._refresh_fingerprints.get(cell.id)
        if previous == fingerprint and previous != (0.0, 0.0):
            return False

        # Three consolidated git invocations replace the previous six:
        #   - rev-list --left-right --count → ahead + behind
        #   - status --porcelain=v2         → dirty + uncommitted/untracked
        #   - diff --numstat                → committed file list + stats
        ahead, behind = await self._ahead_behind(cell)
        dirty, uncommitted_files, untracked_files = await self._status_v2(cell)
        diff_stats, committed_files = await self._diff_numstat(cell)
        # `is_merged` can fan out to several git calls (squash detection).
        # A branch can only become "newly merged" if base has advanced past
        # the fork point — so skip the probe when behind == 0 and we
        # already knew it wasn't merged. Once True, stay True (idempotent).
        if cell.worktree_merged:
            merged = True
        elif behind == 0:
            merged = False
        else:
            merged = await self.is_merged(cell)

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
        self._refresh_fingerprints[cell.id] = self._refresh_fingerprint(cell)
        return changed

    def forget_refresh_state(self, cell_id: str) -> None:
        """Drop the cached refresh fingerprint when an agent goes away."""
        self._refresh_fingerprints.pop(cell_id, None)

    async def _ahead_behind(self, cell) -> tuple[int, int]:
        """One git call: returns (ahead, behind) commits vs base."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
                "rev-list", "--left-right", "--count",
                f"{cell.worktree_base_branch}...HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return (0, 0)
            parts = stdout.decode().split()
            if len(parts) >= 2:
                return (int(parts[1]), int(parts[0]))
        except Exception:
            log.debug("ahead_behind failed for '%s'", cell.name)
        return (0, 0)

    async def _status_v2(self, cell) -> tuple[bool, list[str], list[str]]:
        """One git call: returns (dirty, uncommitted_paths, untracked_paths)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
                "status", "--porcelain=v2", "--untracked-files=normal",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return (False, [], [])
            uncommitted: list[str] = []
            untracked: list[str] = []
            dirty = False
            for raw in stdout.decode().splitlines():
                if not raw:
                    continue
                dirty = True
                tag = raw[0]
                if tag == "1":
                    # ordinary changed entry: "1 XY ... <path>"
                    parts = raw.split(" ", 8)
                    if len(parts) >= 9:
                        uncommitted.append(parts[8])
                elif tag == "2":
                    # rename/copy: "2 XY ... <path>\t<orig>"
                    parts = raw.split(" ", 9)
                    if len(parts) >= 10:
                        path_field = parts[9].split("\t", 1)[0]
                        uncommitted.append(path_field)
                elif tag == "?":
                    # untracked: "? <path>"
                    untracked.append(raw[2:])
            return (dirty, uncommitted, untracked)
        except Exception:
            log.debug("status_v2 failed for '%s'", cell.name)
            return (False, [], [])

    async def _diff_numstat(self, cell) -> tuple[dict, list[str]]:
        """One git call: returns (diff_summary_dict, committed_paths)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
                "diff", "--numstat",
                f"{cell.worktree_base_branch}...HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return ({}, [])
            return _numstat_summary(stdout.decode())
        except Exception:
            log.debug("diff_numstat failed for '%s'", cell.name)
            return ({}, [])

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

    async def _commit_subject(self, repo_root: str, ref: str) -> str:
        code, stdout = await self._git_stdout(
            repo_root, "show", "-s", "--format=%s", ref
        )
        if code != 0:
            return ""
        return stdout.splitlines()[0].strip() if stdout else ""

    async def stale_base_info(self, cell) -> dict:
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
        if stale:
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
            info["warning"] = format_stale_base_warning(info)
        return info

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
                     worktree_name: str = "",
                     state=None) -> Optional[str]:
        """Create a git worktree for the cell.

        Args:
            cell: AgentCell to create the worktree for.
            repo_root: Absolute path to the git repo root.
            base_dir: Directory name for worktrees (relative to repo root).
            base_branch: Branch to fork from (empty = current HEAD).
            symlinks: Relative paths or glob patterns to symlink from repo
                root into worktree.
            worktree_name: Optional custom name for the worktree folder and
                branch suffix.
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

            # Record the base branch for future reference
            if not base_branch:
                base_branch = await self.get_current_branch(repo_root)

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

            # Create configured symlinks
            if symlinks:
                self._create_symlinks(wt_path, repo_root, symlinks)

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

    def _create_symlinks(self, wt_path: str, repo_root: str,
                         symlinks: list[str]) -> None:
        """Create symlinks in worktree pointing to repo root paths."""
        created = []
        for rel_path in self._expand_symlink_paths(repo_root, symlinks):
            target = os.path.join(repo_root, rel_path)
            link = os.path.join(wt_path, rel_path)
            if not os.path.exists(target):
                log.warning("Symlink target does not exist, skipping: %s",
                            target)
                continue
            if os.path.exists(link) or os.path.islink(link):
                log.debug("Path already exists in worktree, skipping "
                          "symlink: %s", link)
                continue
            os.makedirs(os.path.dirname(link), exist_ok=True)
            try:
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

    async def remove_path(self, repo_root: str, worktree_path: str, *,
                          branch: str = "",
                          name: str = "",
                          force: bool = True) -> bool:
        """Remove a git worktree path and optionally delete its branch."""
        if not worktree_path:
            return True
        success = True
        display_name = name or branch or worktree_path
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
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                log.warning("git worktree remove failed for '%s': %s",
                            display_name, stderr.decode().strip())
                success = False
            else:
                log.info("Removed worktree for '%s': %s",
                         display_name, worktree_path)
        except Exception:
            log.exception("Failed to remove worktree for '%s'", display_name)
            success = False

        if branch:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", repo_root,
                    "branch", "-d", branch,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.communicate()
            except Exception:
                log.debug("Could not delete branch %s", branch)
            try:
                from .worktree_streams import invalidate_branch_exists_cache
                invalidate_branch_exists_cache(repo_root, branch)
            except Exception:
                log.debug("Failed to invalidate branch cache", exc_info=True)

        return success

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

    async def remove(self, cell, force: bool = True) -> bool:
        """Remove the git worktree and branch associated with a cell.

        Args:
            cell: AgentCell whose worktree to remove.
            force: If True, force-remove even with uncommitted changes.

        Returns:
            True if successfully removed, False otherwise.
        """
        if not cell.worktree_path:
            return True

        # Resolve repo root — needed for git commands
        repo_root = cell.worktree_repo_root
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path)
        if not repo_root:
            log.warning("Cannot find repo root for worktree '%s' — "
                        "trying parent directory", cell.name)
            repo_root = os.path.dirname(cell.worktree_path)

        success = await self.remove_path(
            repo_root,
            cell.worktree_path,
            branch=cell.worktree_branch,
            name=cell.name,
            force=force,
        )

        if success:
            cell.worktree_path = ""
            cell.worktree_branch = ""
            cell.worktree_repo_root = ""
            cell.worktree_base_branch = ""
            cell.worktree_dirty = False
            cell.worktree_diff = {}
            cell.worktree_changed_files = []
            cell.worktree_checkpoints = 0

        return success

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

    async def has_uncommitted_changes(self, cell) -> bool:
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
            return bool(stdout.decode().strip())
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
                                 paths: list[str] | None = None) -> dict:
        """Return structured per-file diff summary for review planning."""
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

            return {
                "stats": stats,
                "files": files,
                "interesting_files": interesting_files,
                "signal_counts": signal_counts,
            }
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

    async def checkpoint(self, cell, message: str = "") -> Optional[str]:
        """Auto-commit all changes in the worktree. Returns commit SHA."""
        if not cell.worktree_path:
            return None
        try:
            # Seed checkpoint counter from git history if not yet set
            if cell.worktree_checkpoints == 0:
                cell.worktree_checkpoints = await self.count_commits(cell)

            # Stage everything
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path, "add", "-A",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            # Check if there's anything to commit
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
                "diff", "--cached", "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode == 0:
                log.debug("No changes to checkpoint for '%s'", cell.name)
                return None

            if not message:
                n = cell.worktree_checkpoints + 1
                message = f"torque: checkpoint {n} — {cell.name}"

            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
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
                "git", "-C", cell.worktree_path,
                "rev-parse", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            sha = stdout.decode().strip()
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
        if not cell.worktree_path:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
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

    async def is_merged(self, cell) -> bool:
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
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root, "rev-parse",
                cell.worktree_branch, cell.worktree_base_branch,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            shas = out.decode().split()
            if len(shas) == 2 and shas[0] == shas[1]:
                return False

            # Fast path: check if worktree branch is an ancestor of base
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "merge-base", "--is-ancestor",
                cell.worktree_branch, cell.worktree_base_branch,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode == 0:
                return True

            # Slow path: detect squash merges by simulating a merge.
            # If re-merging the branch into base produces base's exact
            # tree, the branch's changes are already incorporated
            # (squash merge, cherry-pick, etc.).  Unlike a direct tip
            # comparison, this works even when base has diverged.

            # 1. Get base branch tree SHA
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "rev-parse",
                f"{cell.worktree_base_branch}^{{tree}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return False
            base_tree = stdout.decode().strip()

            # 2. Simulate merging branch into base (git 2.38+)
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "merge-tree", "--write-tree",
                cell.worktree_base_branch, cell.worktree_branch,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return False
            merge_tree = stdout.decode().strip().split('\n')[0]

            # 3. If the simulated merge produces base's tree unchanged,
            #    the branch's changes are already in base.
            return merge_tree == base_tree
        except Exception:
            log.debug("is_merged check failed for '%s'", cell.name)
            return False

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

    async def check_merge_conflicts(self, cell) -> dict:
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
            base = cell.worktree_base_branch
            branch = cell.worktree_branch
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "merge-tree", "--write-tree", base, branch,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode()
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
            return {"clean": False, "tree_sha": "",
                    "conflicts": conflicts}
        except Exception:
            log.exception("check_merge_conflicts failed for '%s'",
                          cell.name)
            return {"clean": False, "tree_sha": "",
                    "conflicts": [], "error": "Merge check failed"}

    async def server_merge(self, cell, message: str,
                           squash: bool = True) -> dict:
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
            return {"ok": True, "sha": new_sha}
        except Exception:
            log.exception("server_merge failed for '%s'", cell.name)
            return {"ok": False, "error": "Server merge failed"}

    async def reset_to_base(self, cell) -> bool:
        """Reset the worktree branch to the base branch tip.

        Used after merge: all old commits are already incorporated into
        base, so the worktree should start fresh.  This avoids the
        re-merge problem that ``rebase_onto_base`` hits with squash
        merges (where individual commits can't be cleanly replayed on
        top of the squashed result).

        Returns True on success, False on failure.
        """
        if not cell.worktree_path or not cell.worktree_base_branch:
            return False
        base = cell.worktree_base_branch
        try:
            # switch -C moves the current branch to <base> and
            # checks it out — a single porcelain command that
            # updates ref + index + working tree.
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
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
            log.info("Reset '%s' to %s after merge",
                     cell.name, base)
            return True
        except Exception:
            log.exception("reset_to_base failed for '%s'", cell.name)
            return False

    async def rebase_onto_base(self, cell) -> bool:
        """Rebase the worktree branch onto its base branch.

        Returns True on success, False on failure (e.g. conflicts).
        On failure the rebase is aborted so the worktree is left clean.
        """
        if not cell.worktree_path or not cell.worktree_base_branch:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cell.worktree_path,
                "rebase", cell.worktree_base_branch,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode().strip()
                log.warning("Rebase failed for '%s': %s", cell.name, err)
                # Abort to leave the worktree in a clean state
                abort = await asyncio.create_subprocess_exec(
                    "git", "-C", cell.worktree_path,
                    "rebase", "--abort",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await abort.communicate()
                return False
            log.info("Rebased '%s' onto %s",
                     cell.name, cell.worktree_base_branch)
            return True
        except Exception:
            log.exception("Rebase failed for '%s'", cell.name)
            return False

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

        auth = await self._run_gh(worktree_path, "auth", "status")
        if auth.get("returncode") != 0:
            err = auth.get("stderr") or auth.get("stdout") \
                or "gh auth status failed"
            return _worktree_error(
                phase,
                f"GitHub CLI authentication failed: {err}",
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

        first_urls: dict[str, str] = {}
        order: list[str] = []
        for raw_line in (remotes.get("stdout") or "").splitlines():
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
            return _worktree_error(
                phase,
                "PR-based merge requires a GitHub remote; none found.",
            )
        remote = "origin" if "origin" in github_remotes else github_remotes[0]
        return _worktree_ok(
            phase,
            remote=remote,
            url=first_urls.get(remote, ""),
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
        if not worktree_path or not repo_root or not remote or not base_branch:
            return _worktree_error(
                phase,
                "Worktree path, repo root, remote, and base branch are required.",
            )

        remote_ref = f"refs/remotes/{remote}/{base_branch}"
        fetch_refspec = f"+refs/heads/{base_branch}:{remote_ref}"
        fetch = await self._run_capture(
            "git", "-C", worktree_path,
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
        if existing.get("ok") \
                and str(existing.get("state") or "").upper() == "OPEN":
            existing.update({"phase": phase, "existing": True})
            return existing

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
            if "already exists" in err.lower():
                reused = await self.github_pr_view(worktree_path, branch)
                if reused.get("ok"):
                    reused.update({"phase": phase, "existing": True})
                    return reused
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
                        body: str = "") -> dict:
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
        except Exception:
            log.debug("Could not update .gitignore in %s", repo_root)
