"""Nested Merge operations for WorktreeManager."""

from torque.worktree_manager.support import *  # noqa: F403


class NestedMergeMixin:
    """Cohesive nested merge behavior composed by WorktreeManager."""

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
