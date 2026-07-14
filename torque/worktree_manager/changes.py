"""Changes operations for WorktreeManager."""

from torque.worktree_manager.support import *  # noqa: F403


class ChangesMixin:
    """Cohesive changes behavior composed by WorktreeManager."""

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
                                 *, scope_domain=None) -> dict:
        """Return structured per-file diff summary for review planning.

        ``scope_domain`` (optional) is the task's declared domain or an
        explainable diff-scope context from ``build_diff_scope_context``. It is
        used only to add an observability-only ``out_of_scope`` signal/field
        when the diff reaches into a clearly-foreign domain (TORQUE:604 A2).
        It never blocks or changes behavior; when ``None`` no scope flag is
        computed.
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
            scope_context = _normalize_diff_scope_context(scope_domain)
            out_of_scope_info = out_of_scope_diff_classification(
                scope_context, [f["path"] for f in files]
            )
            out_of_scope = out_of_scope_info["paths"]
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
            if scope_context.get("domain") \
                    or scope_context.get("allowed_foreign_domains"):
                summary["scope_classification"] = scope_context
            if out_of_scope:
                summary["out_of_scope"] = {
                    "domain": scope_context.get("domain"),
                    "domain_reason": out_of_scope_info.get(
                        "domain_reason", ""),
                    "paths": out_of_scope,
                    "count": len(out_of_scope),
                    "path_reasons": out_of_scope_info.get(
                        "path_reasons", {}),
                    "rationale": (
                        "Foreign-domain diff paths are flagged only when "
                        "task scope has a clear declared domain and no "
                        "matching cross-surface allowance."
                    ),
                    "digest_line": (
                        f"diff touches {len(out_of_scope)} file(s) outside "
                        f"declared {scope_context.get('domain')} scope: "
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
