"""Merge operations for WorktreeManager."""

from torque.worktree_manager.support import *  # noqa: F403


class MergeMixin:
    """Cohesive merge behavior composed by WorktreeManager."""

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
                check=False,
            )
            if _code != 0:
                return False
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
                check=False,
            )
            if _code != 0:
                return False
            base_tree = stdout.strip()

            # 2. Simulate merging branch into base (git 2.38+)
            _code, stdout, _err = await self._refresh_git(
                repo_root,
                "merge-tree", "--write-tree",
                cell.worktree_base_branch, cell.worktree_branch,
                check=False,
            )
            if _code != 0:
                return False
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
