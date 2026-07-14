"""Nested Lifecycle operations for WorktreeManager."""

from torque.worktree_manager.support import *  # noqa: F403


class NestedLifecycleMixin:
    """Cohesive nested lifecycle behavior composed by WorktreeManager."""

    async def _submodule_name_for_path(self, super_wt: str,
                                       submodule_path: str) -> str:
        """Return the .gitmodules name for *submodule_path*, or the path."""
        wanted = _normalize_repo_rel_path(submodule_path)
        code, stdout = await self._git_stdout(
            super_wt,
            "config",
            "--file",
            ".gitmodules",
            "--get-regexp",
            r"^submodule\..*\.path$",
        )
        if code == 0:
            for line in stdout.splitlines():
                key, sep, value = line.partition(" ")
                if not sep:
                    continue
                if _normalize_repo_rel_path(value) != wanted:
                    continue
                prefix = "submodule."
                suffix = ".path"
                if key.startswith(prefix) and key.endswith(suffix):
                    return key[len(prefix):-len(suffix)] or wanted
        return wanted

    async def _submodule_module_dir(self, repo_root: str, super_wt: str,
                                    submodule_path: str) -> str:
        common_dir = await self._git_common_dir(repo_root)
        if not common_dir:
            return ""
        name = await self._submodule_name_for_path(super_wt, submodule_path)
        parts = [
            part for part in _normalize_repo_rel_path(name).split("/")
            if part
        ]
        if not parts:
            parts = [
                part for part in _normalize_repo_rel_path(submodule_path).split("/")
                if part
            ]
        return os.path.realpath(os.path.join(common_dir, "modules", *parts))

    @staticmethod
    def _resolve_path_from_config_base(base_dir: str, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        path = os.path.expanduser(value)
        if not os.path.isabs(path):
            path = os.path.join(base_dir, path)
        return os.path.realpath(path)

    async def _git_config_file_get(self, config_path: str,
                                   key: str) -> tuple[int, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "config", "--file", config_path, "--get", key,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _stderr = await proc.communicate()
            return proc.returncode, stdout.decode(errors="replace").strip()
        except Exception:
            log.debug("git config --file get failed for %s", config_path)
            return 1, ""

    async def _git_config_file_set(self, config_path: str, key: str,
                                   value: str) -> tuple[int, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "config", "--file", config_path, key, value,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()
            return (
                proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
        except Exception as exc:
            log.debug("git config --file set failed for %s", config_path)
            return 1, str(exc)

    async def _ensure_submodule_module_core_worktree(
            self,
            repo_root: str,
            module_dir: str,
            submodule_path: str) -> dict:
        """Keep a submodule's shared module config pinned to the main checkout.

        Git stores submodule linked worktrees below ``.git/modules/<name>``.
        Commands such as ``git submodule update --init`` invoked from a worker
        superproject checkout can rewrite the module-level ``core.worktree`` to
        that transient worker checkout.  If that worker is later removed, every
        future submodule command can fail while trying to chdir into the
        dangling path.  The shared config must instead keep pointing at the
        superproject's main submodule checkout; linked worker worktrees carry
        their own gitdir metadata under ``worktrees/``.
        """
        module_dir = os.path.realpath(os.path.expanduser(module_dir or ""))
        repo_root = os.path.realpath(os.path.expanduser(repo_root or ""))
        path = _normalize_repo_rel_path(submodule_path)
        main_worktree = self._join_repo_rel(repo_root, path)
        result = {
            "ok": False,
            "changed": False,
            "module_dir": module_dir,
            "config_path": "",
            "expected_worktree": main_worktree,
            "expected_value": "",
            "previous_value": "",
            "previous_resolved": "",
            "error": "",
        }
        if not module_dir or not repo_root or not path:
            result["error"] = "missing module_dir, repo_root, or submodule path"
            return result
        config_path = os.path.join(module_dir, "config")
        result["config_path"] = config_path
        if not os.path.isfile(config_path):
            result["error"] = f"missing submodule module config: {config_path}"
            return result

        expected_value = os.path.relpath(main_worktree, module_dir)
        expected_value = expected_value.replace(os.sep, "/")
        result["expected_value"] = expected_value

        code, current = await self._git_config_file_get(
            config_path,
            "core.worktree",
        )
        if code == 0:
            result["previous_value"] = current
            result["previous_resolved"] = self._resolve_path_from_config_base(
                module_dir,
                current,
            )
            if result["previous_resolved"] == main_worktree:
                result["ok"] = True
                return result

        set_code, err = await self._git_config_file_set(
            config_path,
            "core.worktree",
            expected_value,
        )
        if set_code != 0:
            result["error"] = err or "could not set core.worktree"
            return result
        result["ok"] = True
        result["changed"] = True
        if result["previous_resolved"] and result["previous_resolved"] != main_worktree:
            log.warning(
                "Repaired submodule module core.worktree for %s: %s -> %s",
                path,
                result["previous_resolved"],
                main_worktree,
            )
        return result

    async def _ensure_submodule_module_core_worktree_for_info(
            self,
            info: dict) -> dict:
        return await self._ensure_submodule_module_core_worktree(
            info.get("repo_root", ""),
            info.get("module_dir", ""),
            info.get("path", ""),
        )

    async def _gitlink_sha(self, super_wt: str, ref: str,
                           submodule_path: str) -> str:
        """Return the gitlink SHA for a submodule path at *ref*."""
        path = _normalize_repo_rel_path(submodule_path)
        if not super_wt or not ref or not path:
            return ""
        code, stdout = await self._git_stdout(
            super_wt,
            "ls-tree",
            ref,
            "--",
            path,
        )
        if code != 0 or not stdout:
            return ""
        line = stdout.splitlines()[0].strip()
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "160000":
            return parts[2]
        return ""

    async def _nested_submodule_infos(self, repo_root: str, super_wt: str,
                                      worktree_submodules,
                                      *,
                                      ref: str = "HEAD",
                                      require_worktree: bool = False,
                                      strict: bool = False) -> list[dict]:
        """Resolve configured submodules that are present in the super worktree."""
        infos: list[dict] = []
        for sub_path in _normalize_worktree_submodules(worktree_submodules):
            gitlink_sha = await self._gitlink_sha(super_wt, ref, sub_path)
            if not gitlink_sha:
                continue
            module_dir = await self._submodule_module_dir(
                repo_root,
                super_wt,
                sub_path,
            )
            sub_wt_path = self._join_repo_rel(super_wt, sub_path)
            if require_worktree and not (
                await self._is_nested_submodule_linked_worktree(
                    module_dir,
                    sub_wt_path,
                )
            ):
                continue
            await self._ensure_submodule_module_core_worktree(
                repo_root,
                module_dir,
                sub_path,
            )
            module_ok = await self._is_git_repo(module_dir)
            if not module_ok:
                message = (
                    f"Configured submodule '{sub_path}' is present but "
                    f"shared module store is missing: {module_dir}"
                )
                if strict:
                    raise RuntimeError(message)
                log.warning(message)
                continue
            infos.append({
                "path": sub_path,
                "repo_root": os.path.realpath(os.path.expanduser(repo_root)),
                "worktree_path": sub_wt_path,
                "module_dir": module_dir,
                "gitlink_sha": gitlink_sha,
            })
        return infos

    async def _nested_submodule_infos_for_cell(self, cell,
                                               worktree_submodules,
                                               *,
                                               require_worktree: bool = True,
                                               strict: bool = False) -> list[dict]:
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths or not cell or not getattr(cell, "worktree_path", ""):
            return []
        repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path) or ""
        if not repo_root:
            return []
        return await self._nested_submodule_infos(
            repo_root,
            cell.worktree_path,
            paths,
            ref="HEAD",
            require_worktree=require_worktree,
            strict=strict,
        )

    async def _resolve_nested_submodule_branch(self, module_dir: str,
                                               submodule_path: str,
                                               super_branch: str) -> str:
        base = _nested_submodule_branch_name(submodule_path, super_branch)
        if not await self._branch_exists(module_dir, base):
            return base
        suffix = 2
        while True:
            candidate = f"{base}-{suffix}"
            if not await self._branch_exists(module_dir, candidate):
                return candidate
            suffix += 1

    async def _create_nested_submodule_worktrees(self, repo_root: str,
                                                 super_wt: str,
                                                 super_branch: str,
                                                 worktree_submodules) -> list[dict]:
        """Create linked worktrees for configured submodules in a super worktree."""
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return []

        created: list[dict] = []
        try:
            infos = await self._nested_submodule_infos(
                repo_root,
                super_wt,
                paths,
                ref="HEAD",
                strict=True,
            )
            for info in infos:
                sub_wt_path = info["worktree_path"]
                if os.path.lexists(sub_wt_path):
                    if not os.path.isdir(sub_wt_path):
                        raise RuntimeError(
                            f"Submodule path is not a directory: {sub_wt_path}"
                        )
                    if os.listdir(sub_wt_path):
                        raise RuntimeError(
                            "Submodule worktree target is not empty: "
                            f"{sub_wt_path}"
                        )
                else:
                    os.makedirs(os.path.dirname(sub_wt_path), exist_ok=True)

                sub_branch = await self._resolve_nested_submodule_branch(
                    info["module_dir"],
                    info["path"],
                    super_branch,
                )
                await self._ensure_submodule_module_core_worktree_for_info(info)
                cmd = [
                    "git", "-C", info["module_dir"],
                    "worktree", "add",
                    "-b", sub_branch,
                    sub_wt_path,
                    info["gitlink_sha"],
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                entry = {
                    **info,
                    "branch": sub_branch,
                    "git_returncode": proc.returncode,
                    "git_stdout": stdout.decode(errors="replace").strip(),
                    "git_stderr": stderr.decode(errors="replace").strip(),
                }
                entry["module_core_worktree_after_add"] = (
                    await self._ensure_submodule_module_core_worktree_for_info(info)
                )
                if proc.returncode != 0:
                    raise RuntimeError(
                        "git submodule worktree add failed for "
                        f"{info['path']}: {entry['git_stderr']}"
                    )
                created.append(entry)
                if not entry["module_core_worktree_after_add"].get("ok"):
                    raise RuntimeError(
                        "git submodule worktree add left shared "
                        f"core.worktree invalid for {info['path']}: "
                        f"{entry['module_core_worktree_after_add'].get('error', '')}"
                    )
                code, _out, err = await self._git_run(
                    sub_wt_path,
                    "reset",
                    "--hard",
                    info["gitlink_sha"],
                )
                entry["pinned_gitlink_sha"] = info["gitlink_sha"]
                entry["pin_returncode"] = code
                if code != 0:
                    raise RuntimeError(
                        "git submodule worktree pin failed for "
                        f"{info['path']} at {info['gitlink_sha'][:12]}: {err}"
                    )
                entry["module_core_worktree_after_pin"] = (
                    await self._ensure_submodule_module_core_worktree_for_info(info)
                )
                if not entry["module_core_worktree_after_pin"].get("ok"):
                    raise RuntimeError(
                        "git submodule worktree pin left shared "
                        f"core.worktree invalid for {info['path']}: "
                        f"{entry['module_core_worktree_after_pin'].get('error', '')}"
                    )
                log.info(
                    "Created nested submodule worktree %s on %s",
                    sub_wt_path,
                    sub_branch,
                )
        except Exception:
            for entry in reversed(created):
                try:
                    await self._remove_one_nested_submodule_worktree(
                        entry,
                        force=True,
                    )
                except Exception:
                    log.debug(
                        "Failed to clean up nested submodule worktree %s",
                        entry.get("worktree_path"),
                        exc_info=True,
                    )
            raise

        return created

    async def _cat_file_commit_exists(self, directory: str, sha: str) -> bool:
        if not directory or not sha:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", directory, "cat-file", "-e", f"{sha}^{{commit}}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    async def _create_preserved_nested_submodule_ref(self, info: dict,
                                                     head: str = "") -> dict:
        """Create a stable branch ref for a nested-submodule HEAD."""
        module_dir = info.get("module_dir", "")
        head = (
            head
            or await self.rev_parse(info.get("worktree_path", ""), "HEAD")
            or ""
        )
        result = {
            "head": head,
            "branch": "",
            "branch_created": False,
        }
        if not module_dir or not head:
            return result
        base = (
            f"torque/preserved/"
            f"{_slugify_worktree_name(info.get('path', '').replace('/', '-'), max_len=60) or 'submodule'}/"
            f"{head[:12] or 'head'}"
        )
        preserve_branch = base
        suffix = 2
        while await self._branch_exists(module_dir, preserve_branch):
            existing = await self.rev_parse(module_dir, preserve_branch) or ""
            if existing == head:
                result["branch"] = preserve_branch
                return result
            preserve_branch = f"{base}-{suffix}"
            suffix += 1
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", module_dir, "branch", preserve_branch, head,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                "Could not preserve nested submodule HEAD "
                f"{head[:12]}: {stderr.decode(errors='replace').strip()}"
            )
        result.update({
            "branch": preserve_branch,
            "branch_created": True,
        })
        return result

    async def _preserve_nested_submodule_head(self, info: dict) -> dict:
        """Ensure the nested submodule HEAD remains reachable by a branch ref."""
        sub_wt_path = info.get("worktree_path", "")
        module_dir = info.get("module_dir", "")
        head = await self.rev_parse(sub_wt_path, "HEAD") or ""
        branch = (await self.get_current_branch(sub_wt_path) or "").strip()
        created_branch = False
        preserve_branch = branch if branch and branch != "HEAD" else ""
        if not preserve_branch or not await self._branch_exists(
                module_dir, preserve_branch):
            preserved = await self._create_preserved_nested_submodule_ref(
                info,
                head,
            )
            preserve_branch = preserved.get("branch", "")
            created_branch = bool(preserved.get("branch_created"))

        return {
            "head": head,
            "branch": preserve_branch,
            "branch_created": created_branch,
            "head_reachable_before_remove": (
                await self._cat_file_commit_exists(module_dir, head)
            ),
        }

    async def _remove_one_nested_submodule_worktree(self, info: dict, *,
                                                   force: bool = True) -> dict:
        """Remove one linked submodule worktree while preserving its branch ref."""
        sub_wt_path = info.get("worktree_path", "")
        module_dir = info.get("module_dir", "")
        result = {
            "path": info.get("path", ""),
            "worktree_path": sub_wt_path,
            "module_dir": module_dir,
            "ok": False,
            "worktree_removed": False,
            "branch_preserved": False,
            "branch": "",
            "head": "",
            "git_returncode": None,
            "git_stdout": "",
            "git_stderr": "",
            "pre_state": {},
            "post_state": {},
            "module_core_worktree": {},
            "message": "",
        }
        if info.get("repo_root"):
            result["module_core_worktree"] = (
                await self._ensure_submodule_module_core_worktree_for_info(info)
            )
        guard = await self._preserve_nested_submodule_head(info)
        result.update({
            "branch": guard.get("branch", ""),
            "head": guard.get("head", ""),
            "branch_created": guard.get("branch_created", False),
            "head_reachable_before_remove": guard.get(
                "head_reachable_before_remove", False),
        })
        result["pre_state"] = await self.removal_state(
            module_dir,
            sub_wt_path,
            branch=result["branch"],
        )

        try:
            remove_cwd = module_dir or sub_wt_path
            cmd = [
                "git", "-C", remove_cwd,
                "worktree", "remove", sub_wt_path,
            ]
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
        except Exception:
            log.exception("Failed to remove nested submodule worktree %s",
                          sub_wt_path)
            result["git_returncode"] = -1

        if info.get("repo_root"):
            result["module_core_worktree"] = (
                await self._ensure_submodule_module_core_worktree_for_info(info)
            )
        result["post_state"] = await self.removal_state(
            module_dir,
            sub_wt_path,
            branch=result["branch"],
        )
        result["worktree_removed"] = (
            not bool(result["post_state"].get("path_exists"))
            and not bool(result["post_state"].get("listed"))
        )
        branch_exists = bool(result["post_state"].get("branch_exists"))
        head_reachable = await self._cat_file_commit_exists(
            module_dir,
            result["head"],
        )
        result["head_reachable_after_remove"] = head_reachable
        result["branch_preserved"] = bool(branch_exists and head_reachable)
        result["ok"] = bool(
            result["worktree_removed"] and result["branch_preserved"]
            and (
                not result["module_core_worktree"]
                or result["module_core_worktree"].get("ok")
            )
        )
        result["message"] = (
            "Nested submodule worktree removed and branch preserved"
            if result["ok"]
            else "Nested submodule worktree removal did not preserve expected state"
        )
        return result

    async def _remove_nested_submodule_worktrees(self, repo_root: str,
                                                super_wt: str,
                                                worktree_submodules,
                                                *,
                                                force: bool = True) -> list[dict]:
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return []
        infos = await self._nested_submodule_infos(
            repo_root,
            super_wt,
            paths,
            ref="HEAD",
            require_worktree=True,
            strict=False,
        )
        removed = []
        for info in infos:
            removed.append(
                await self._remove_one_nested_submodule_worktree(
                    info,
                    force=force,
                )
            )
        return removed

    def _filter_numstat_paths(self, text: str,
                              skip_paths: set[str]) -> str:
        if not skip_paths:
            return text
        lines = []
        for line in str(text or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and _normalize_repo_rel_path(parts[2]) in skip_paths:
                continue
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _prefix_numstat_paths(text: str, prefix: str) -> str:
        prefix = _normalize_repo_rel_path(prefix)
        lines = []
        for line in str(text or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            parts[2] = f"{prefix}/{parts[2]}" if prefix else parts[2]
            lines.append("\t".join(parts))
        return "\n".join(lines)

    async def _nested_submodule_numstat(self, cell,
                                        worktree_submodules) -> tuple[str, set[str]]:
        """Return prefixed numstat text for configured nested submodule worktrees."""
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return "", set()
        repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
        if not repo_root:
            repo_root = await self.get_repo_root(cell.worktree_path) or ""
        if not repo_root:
            return "", set()
        infos = await self._nested_submodule_infos(
            repo_root,
            cell.worktree_path,
            paths,
            ref="HEAD",
            require_worktree=True,
            strict=False,
        )
        chunks: list[str] = []
        replaced: set[str] = set()
        for info in infos:
            base_sha = await self._gitlink_sha(
                cell.worktree_path,
                cell.worktree_base_branch,
                info["path"],
            )
            args = ["diff", "--numstat"]
            if base_sha:
                # Compare the current nested worktree state (committed and
                # tracked uncommitted edits) against the superproject base
                # gitlink. This replaces the superproject's coarse gitlink
                # numstat with file-level submodule stats.
                args.append(base_sha)
            _code, stdout, _stderr = await self._refresh_git(
                info["worktree_path"],
                *args,
            )
            if not stdout:
                continue
            prefixed = self._prefix_numstat_paths(stdout, info["path"])
            if prefixed:
                chunks.append(prefixed)
                replaced.add(info["path"])
        return "\n".join(chunks), replaced

    async def nested_submodule_head_states(self, cell,
                                           worktree_submodules) -> list[dict]:
        """Return current branch/head/gitlink metadata for configured submodules."""
        paths = _normalize_worktree_submodules(worktree_submodules)
        if not paths:
            return []
        repo_root = str(getattr(cell, "worktree_repo_root", "") or "").strip()
        if not repo_root and getattr(cell, "worktree_path", ""):
            repo_root = await self.get_repo_root(cell.worktree_path) or ""
        if not repo_root:
            return []
        infos = await self._nested_submodule_infos(
            repo_root,
            getattr(cell, "worktree_path", "") or "",
            paths,
            ref="HEAD",
            require_worktree=True,
            strict=False,
        )
        states: list[dict] = []
        base = str(getattr(cell, "worktree_base_branch", "") or "").strip()
        for info in infos:
            sub_wt = info.get("worktree_path", "")
            branch = await self.get_current_branch(sub_wt)
            head = await self.rev_parse(sub_wt, "HEAD") or ""
            states.append({
                "path": info.get("path", ""),
                "repo_root": info.get("module_dir", ""),
                "branch": branch if branch != "HEAD" else "",
                "base_branch": base,
                "commit_sha": head,
                "gitlink_sha": info.get("gitlink_sha", ""),
            })
        return states
