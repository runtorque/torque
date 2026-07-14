"""Refresh operations for WorktreeManager."""

from torque.worktree_manager.support import *  # noqa: F403


class RefreshMixin:
    """Cohesive refresh behavior composed by WorktreeManager."""

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

    async def _refresh_await(self, label: str, awaitable,
                             *, timeout: float | None = None):
        """Bound a refresh-only async helper that may call legacy git wrappers."""
        deadline = float(timeout or self.refresh_git_timeout_seconds)
        try:
            return await asyncio.wait_for(awaitable, timeout=deadline)
        except WorktreeRefreshError:
            raise
        except asyncio.TimeoutError as exc:
            raise WorktreeRefreshError(
                "timeout",
                f"{label} exceeded {deadline:.1f}s",
                command=label,
            ) from exc

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
        infos = await self._refresh_await(
            "nested_submodule_infos:ahead_behind",
            self._nested_submodule_infos(
                repo_root,
                cell.worktree_path,
                submodule_paths,
                ref="HEAD",
                require_worktree=True,
                strict=False,
            ),
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
                            and await self._refresh_await(
                                "nested_submodule_gitlink_drift",
                                self._is_clean_submodule_gitlink_drift(
                                    cell,
                                    path,
                                    submodule_paths,
                                    status_xy=parts[1] if len(parts) > 1 else "",
                                ),
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
                await self._refresh_await(
                    "nested_submodule_numstat",
                    self._nested_submodule_numstat(
                        cell,
                        worktree_submodules,
                    ),
                    timeout=self.refresh_git_timeout_seconds * 2,
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
