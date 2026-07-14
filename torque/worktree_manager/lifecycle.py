"""Lifecycle operations for WorktreeManager."""

from torque.worktree_manager.support import *  # noqa: F403


class LifecycleMixin:
    """Cohesive lifecycle behavior composed by WorktreeManager."""

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
        remote_base_ref = f"origin/{base}" if base else ""
        remote_base_head = (
            await self.rev_parse(repo_root, f"refs/remotes/{remote_base_ref}")
            if remote_base_ref else ""
        ) or ""
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
            "remote_base_ref": remote_base_ref,
            "remote_base_head": remote_base_head,
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
                from torque.worktree_streams import invalidate_branch_exists_cache
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
                from torque.worktree_streams import invalidate_branch_exists_cache
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
