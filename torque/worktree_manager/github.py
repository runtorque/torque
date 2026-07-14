"""Github operations for WorktreeManager."""

from torque.worktree_manager.support import *  # noqa: F403


class GithubMixin:
    """Cohesive github behavior composed by WorktreeManager."""

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
