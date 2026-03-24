"""Git worktree lifecycle management for Loom agents."""

import asyncio
import os
import re
from typing import Optional

from .config import log


class WorktreeManager:
    """Manages git worktrees for agent isolation."""

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

    async def create(self, cell, repo_root: str,
                     base_dir: str = ".loom/worktrees",
                     base_branch: str = "") -> Optional[str]:
        """Create a git worktree for the cell.

        Args:
            cell: AgentCell to create the worktree for.
            repo_root: Absolute path to the git repo root.
            base_dir: Directory name for worktrees (relative to repo root).
            base_branch: Branch to fork from (empty = current HEAD).

        Returns:
            Absolute path to the worktree, or None on failure.
        """
        try:
            slug = re.sub(r"[^a-z0-9-]", "-",
                          cell.name.lower().strip())[:30].strip("-")
            short_id = cell.id[:7]
            branch = f"loom/{slug}-{short_id}"
            wt_path = os.path.join(repo_root, base_dir, cell.id)

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

            return wt_path
        except Exception:
            log.exception("Failed to create worktree for '%s'", cell.name)
            return None

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

    async def checkpoint(self, cell, message: str = "") -> Optional[str]:
        """Auto-commit all changes in the worktree. Returns commit SHA."""
        if not cell.worktree_path:
            return None
        try:
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

    async def _ensure_gitignore(self, repo_root: str):
        """Add .loom/ to .gitignore if not already present."""
        gitignore = os.path.join(repo_root, ".gitignore")
        marker = ".loom/"
        try:
            content = ""
            if os.path.exists(gitignore):
                with open(gitignore) as f:
                    content = f.read()
            if marker not in content.splitlines():
                with open(gitignore, "a") as f:
                    if content and not content.endswith("\n"):
                        f.write("\n")
                    f.write(f"{marker}\n")
                log.info("Added %s to .gitignore in %s", marker, repo_root)
        except Exception:
            log.debug("Could not update .gitignore in %s", repo_root)
