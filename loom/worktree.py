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
        if not cell.worktree_path or not cell.worktree_base_branch:
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

            # 5. Fast-forward base branch to the new commit
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
        """Add Loom-managed paths to .gitignore if not already present."""
        gitignore = os.path.join(repo_root, ".gitignore")
        entries = [
            ".loom/worktrees/",
            ".claude/settings.local.json",
            ".claude/instructions.md",
            ".mcp.json",
            ".claude/skills/loom-*/",
            ".codex/config.toml",
            ".codex/hooks.json",
        ]
        try:
            content = ""
            if os.path.exists(gitignore):
                with open(gitignore) as f:
                    content = f.read()
            lines = content.splitlines()
            added = []
            for entry in entries:
                if entry not in lines:
                    added.append(entry)
            if added:
                with open(gitignore, "a") as f:
                    if content and not content.endswith("\n"):
                        f.write("\n")
                    for entry in added:
                        f.write(f"{entry}\n")
                log.info("Added %s to .gitignore in %s",
                         ", ".join(added), repo_root)
        except Exception:
            log.debug("Could not update .gitignore in %s", repo_root)
