"""Git worktree lifecycle management for Loom agents."""

import asyncio
import os
import re
from typing import Optional

from .config import log

# Files Loom injects into user repos (adapters, MCP, hooks, skills).
# These must be excluded from git so they don't pollute `git status`.
LOOM_EXCLUDE_ENTRIES = [
    ".mcp.json",
    ".claude/settings.local.json",
    ".claude/instructions.md",
    ".claude/skills/loom-*/",
    ".codex/config.toml",
    ".codex/hooks.json",
    ".codex/AGENTS.md",
    ".loom/loom-system-prompt-*.md",
]

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
    """Add Loom-injected filenames to .git/info/exclude if not present.

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

        to_add = [e for e in LOOM_EXCLUDE_ENTRIES if e not in existing]
        if to_add:
            with open(exclude, "a") as f:
                for entry in to_add:
                    f.write(f"{entry}\n")
            log.debug("Added %d entries to git exclude: %s",
                      len(to_add), exclude)
    except Exception:
        log.debug("Could not update git exclude in %s", directory)


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


def _resolve_worktree_base_path(repo_root: str, base_dir: str) -> str:
    """Return the absolute base directory under which worktrees are created."""
    repo_root = os.path.realpath(os.path.expanduser(repo_root))
    base_dir = os.path.expanduser(base_dir or ".loom/worktrees")
    if os.path.isabs(base_dir):
        return os.path.realpath(base_dir)
    return os.path.realpath(os.path.join(repo_root, base_dir))


class WorktreeManager:
    """Manages git worktrees for agent isolation."""

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

    async def get_repo_root(self, directory: str) -> Optional[str]:
        """Find the git repo root for a directory. Returns None if not a repo."""
        directory = os.path.expanduser(directory)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", directory, "rev-parse", "--show-toplevel",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None
            return stdout.decode().strip()
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
                                       worktree_name: str = "") -> tuple[str, str]:
        """Choose the final worktree branch/path pair for this creation."""
        requested = _slugify_worktree_name(worktree_name)
        if not requested:
            slug = _slugify_worktree_name(cell.name, max_len=30) or "unnamed"
            short_id = cell.id[:7]
            branch = f"loom/{slug}-{short_id}"
            wt_path = os.path.join(
                _resolve_worktree_base_path(repo_root, base_dir),
                cell.id,
            )
            return branch, wt_path

        base_path = _resolve_worktree_base_path(repo_root, base_dir)
        candidate = requested
        suffix_index = 2
        while True:
            branch = f"loom/{candidate}"
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
                     base_dir: str = ".loom/worktrees",
                     base_branch: str = "",
                     symlinks: list[str] | None = None,
                     worktree_name: str = "") -> Optional[str]:
        """Create a git worktree for the cell.

        Args:
            cell: AgentCell to create the worktree for.
            repo_root: Absolute path to the git repo root.
            base_dir: Directory name for worktrees (relative to repo root).
            base_branch: Branch to fork from (empty = current HEAD).
            symlinks: Relative paths to symlink from repo root into worktree.
            worktree_name: Optional custom name for the worktree folder and
                branch suffix.

        Returns:
            Absolute path to the worktree, or None on failure.
        """
        try:
            branch, wt_path = await self._resolve_worktree_target(
                cell,
                repo_root,
                base_dir,
                worktree_name=worktree_name,
            )
            os.makedirs(os.path.dirname(wt_path), exist_ok=True)

            # Ensure .loom directory exists
            loom_dir = os.path.join(repo_root, ".loom")
            os.makedirs(loom_dir, exist_ok=True)

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
            log.info("Created worktree for '%s': %s (branch %s, base %s)",
                     cell.name, wt_path, branch, base_branch)

            # Add .loom/ to .gitignore if not already there
            await self._ensure_gitignore(repo_root)

            # Create configured symlinks
            if symlinks:
                self._create_symlinks(wt_path, repo_root, symlinks)

            return wt_path
        except Exception:
            log.exception("Failed to create worktree for '%s'", cell.name)
            return None

    def _create_symlinks(self, wt_path: str, repo_root: str,
                         symlinks: list[str]) -> None:
        """Create symlinks in worktree pointing to repo root paths."""
        created = []
        for rel_path in symlinks:
            rel_path = rel_path.strip().strip("/")
            if not rel_path or ".." in rel_path:
                log.warning("Skipping invalid symlink path: %s", rel_path)
                continue
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

        success = True
        try:
            cmd = ["git", "-C", repo_root,
                   "worktree", "remove", cell.worktree_path]
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
                            cell.name, stderr.decode().strip())
                success = False
            else:
                log.info("Removed worktree for '%s': %s",
                         cell.name, cell.worktree_path)
        except Exception:
            log.exception("Failed to remove worktree for '%s'", cell.name)
            success = False

        # Try to delete the branch
        if cell.worktree_branch:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", repo_root,
                    "branch", "-d", cell.worktree_branch,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.communicate()
                # -d may fail if not fully merged; that's OK
            except Exception:
                log.debug("Could not delete branch %s", cell.worktree_branch)

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

    async def diff_summary(self, cell) -> dict:
        """Return diff stats for the worktree vs its base branch.

        Returns:
            {"files": int, "insertions": int, "deletions": int}
            Empty dict on failure.
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

            files = 0
            insertions = 0
            deletions = 0
            for line in stdout.decode().strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    try:
                        ins = int(parts[0]) if parts[0] != "-" else 0
                        dels = int(parts[1]) if parts[1] != "-" else 0
                        insertions += ins
                        deletions += dels
                        files += 1
                    except ValueError:
                        continue

            return {"files": files, "insertions": insertions,
                    "deletions": deletions}
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
                message = f"loom: checkpoint {n} — {cell.name}"

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
            hdr_delim = "---LOOM_COMMIT---"
            body_delim = "---LOOM_BODY---"
            body_end = "---LOOM_BODY_END---"
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

    def _parse_merge_tree_conflicts(self, output: str) -> list[dict]:
        """Parse conflict info from ``git merge-tree`` output."""
        conflicts: list[dict] = []
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("CONFLICT"):
                continue
            # Format: CONFLICT (type): description
            reason = ""
            path = ""
            paren_start = line.find("(")
            paren_end = line.find(")")
            if paren_start != -1 and paren_end != -1:
                reason = line[paren_start + 1:paren_end]
            # Extract file path — typically the last space-separated token,
            # or the path after "Merge conflict in "
            if "Merge conflict in " in line:
                path = line.split("Merge conflict in ", 1)[1].strip()
            elif " deleted in " in line:
                # "CONFLICT (modify/delete): foo.py deleted in ..."
                colon_pos = line.find(": ")
                if colon_pos != -1:
                    rest = line[colon_pos + 2:]
                    path = rest.split(" deleted in ")[0].strip()
            else:
                # Fallback: last token after ": "
                colon_pos = line.find(": ")
                if colon_pos != -1:
                    rest = line[colon_pos + 2:].strip()
                    parts = rest.rsplit(" ", 1)
                    path = parts[-1] if parts else rest
            conflicts.append({"path": path, "reason": reason})
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
            conflicts = self._parse_merge_tree_conflicts(output)
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

    async def create_pr(self, cell, title: str = "",
                        body: str = "") -> dict:
        """Push the worktree branch and create a GitHub PR.

        Returns dict with 'url' on success or 'error' on failure.
        """
        if not cell.worktree_path or not cell.worktree_branch:
            return {"error": "No worktree branch found for this agent."}

        wt = cell.worktree_path
        branch = cell.worktree_branch
        base = cell.worktree_base_branch or "main"

        # Check gh CLI is available
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "--version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode != 0:
                return {"error": "GitHub CLI (gh) is not installed."}
        except FileNotFoundError:
            return {"error": "GitHub CLI (gh) is not installed."}

        # Check this is a GitHub repo
        proc = await asyncio.create_subprocess_exec(
            "gh", "-C", wt, "repo", "view", "--json", "name",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode().strip()
            if "not a git repository" in err.lower():
                return {"error": "Not a git repository."}
            return {"error": f"Not a GitHub repository: {err}"}

        # Check branch has commits ahead of base
        count = await self.count_commits(cell)
        if count == 0:
            return {"error": f"Branch {branch} has no commits ahead "
                             f"of {base}."}

        # Push the branch
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", wt, "push", "-u", "origin", branch,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode().strip()
            return {"error": f"Failed to push branch: {err}"}

        # Create PR
        cmd = ["gh", "-C", wt, "pr", "create",
               "--base", base,
               "--head", branch,
               "--title", title or branch,
               "--body", body or ""]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode().strip()
            if "already exists" in err.lower():
                # PR already exists — try to get its URL
                p2 = await asyncio.create_subprocess_exec(
                    "gh", "-C", wt, "pr", "view", branch,
                    "--json", "url", "-q", ".url",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                out2, _ = await p2.communicate()
                if p2.returncode == 0 and out2.decode().strip():
                    return {"url": out2.decode().strip(),
                            "existing": True}
            return {"error": f"Failed to create PR: {err}"}

        url = stdout.decode().strip()
        log.info("Created PR for '%s': %s", cell.name, url)
        return {"url": url}

    async def _ensure_gitignore(self, repo_root: str):
        """Add .loom/worktrees/ to .gitignore if not already present.

        Only the worktree directory belongs in .gitignore (shared across
        clones).  All other Loom-injected files are excluded via
        .git/info/exclude (per-checkout, not version-controlled).
        """
        gitignore = os.path.join(repo_root, ".gitignore")
        entry = ".loom/worktrees/"
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
            # Exclude Loom-injected files via .git/info/exclude
            ensure_git_exclude(repo_root)
        except Exception:
            log.debug("Could not update .gitignore in %s", repo_root)
