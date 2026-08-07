"""Cached Git probes used by worktree stream synthesis."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from typing import Iterable

from .worktree_boundaries import task_boundary


# `_branch_exists_locally` used to shell out to `git show-ref` per branch per
# stream on every broadcast, forking dozens of times per second on the asyncio
# event loop. We now batch the lookup: a single `git for-each-ref` per repo
# populates a per-repo set of branches, and stream computation reads from the
# set. Callers should `await prefill_branch_exists_async(...)` before entering
# the sync compute path so the cache is warm and no subprocess runs inline.
# The per-branch cache is retained as a fallback for the rare sync-context
# entry (CLI, tests) where no prefill has happened.
_BRANCH_EXISTS_TTL_SECONDS = 60.0
# Bound and limit the stream branch-exists prefill git call. This path feeds
# state snapshots, so it must never let slow branch metadata block the daemon.
_BRANCH_EXISTS_GIT_TIMEOUT_SECONDS = 5.0
_BRANCH_EXISTS_MAX_CONCURRENT_REFRESHES = 4
_branch_exists_ttl_cache: dict[tuple[str, str], tuple[float, bool]] = {}
_repo_branches_cache: dict[str, tuple[float, frozenset[str]]] = {}
_repo_branches_inflight: dict[str, asyncio.Future] = {}
_repo_branches_semaphore = asyncio.Semaphore(_BRANCH_EXISTS_MAX_CONCURRENT_REFRESHES)

# Merge readiness is a read model, but it must not guess that a branch can be
# merged.  Keep the expensive merge-tree probe off the synchronous snapshot
# path and warm this small, short-lived cache from its async callers.
_MERGE_READINESS_TTL_SECONDS = 2.0
_MERGE_READINESS_GIT_TIMEOUT_SECONDS = 8.0
_MERGE_READINESS_MAX_CONCURRENT_REFRESHES = 4
_merge_readiness_ttl_cache: dict[
    tuple[str, str, str], tuple[float, dict]
] = {}
_merge_readiness_semaphore = asyncio.Semaphore(
    _MERGE_READINESS_MAX_CONCURRENT_REFRESHES
)


def _normalize_repo_root(repo_root: str) -> str:
    value = str(repo_root or "").strip()
    if not value:
        return ""
    return os.path.realpath(os.path.expanduser(value))


def invalidate_branch_exists_cache(repo_root: str = "", branch: str = "") -> None:
    """Drop cached branch-existence entries.

    With no arguments, clear the whole cache. With ``repo_root`` set, clear
    entries for that repo. With both set, clear just that entry. Call this
    when Torque itself creates or deletes a branch so the next lookup is
    fresh instead of waiting out the TTL.
    """
    repo_root = _normalize_repo_root(repo_root) if repo_root else ""
    branch = str(branch or "").strip()
    if not repo_root:
        _branch_exists_ttl_cache.clear()
        _repo_branches_cache.clear()
        _merge_readiness_ttl_cache.clear()
        return
    if branch:
        _branch_exists_ttl_cache.pop((repo_root, branch), None)
    else:
        for key in [k for k in _branch_exists_ttl_cache if k[0] == repo_root]:
            _branch_exists_ttl_cache.pop(key, None)
    _repo_branches_cache.pop(repo_root, None)
    for key in [
            key for key in _merge_readiness_ttl_cache
            if key[0] == repo_root and (not branch or key[1] == branch)
    ]:
        _merge_readiness_ttl_cache.pop(key, None)


def _merge_readiness_cache_key(repo_root: str, branch: str,
                               base_branch: str) -> tuple[str, str, str]:
    return (
        _normalize_repo_root(repo_root),
        str(branch or "").strip(),
        str(base_branch or "").strip(),
    )


def _merge_readiness_cache_get(repo_root: str, branch: str,
                               base_branch: str) -> dict:
    key = _merge_readiness_cache_key(repo_root, branch, base_branch)
    if not all(key):
        return {}
    cached = _merge_readiness_ttl_cache.get(key)
    if not cached or cached[0] <= time.monotonic():
        return {}
    return dict(cached[1])


def _merge_readiness_cache_put(repo_root: str, branch: str,
                               base_branch: str, info: dict) -> None:
    key = _merge_readiness_cache_key(repo_root, branch, base_branch)
    if not all(key):
        return
    _merge_readiness_ttl_cache[key] = (
        time.monotonic() + _MERGE_READINESS_TTL_SECONDS,
        dict(info or {}),
    )


async def _list_repo_branches_async(repo_root: str) -> frozenset[str]:
    """Run one `git for-each-ref` and return the set of local branch names."""
    if not repo_root or not os.path.isdir(repo_root):
        return frozenset()
    proc = None
    try:
        async with _repo_branches_semaphore:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root,
                "for-each-ref", "--format=%(refname:short)", "refs/heads/",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=_BRANCH_EXISTS_GIT_TIMEOUT_SECONDS,
            )
        if proc.returncode != 0:
            return frozenset()
        return frozenset(
            line.strip()
            for line in stdout.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        )
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except Exception:
                pass
        return frozenset()
    except (OSError, asyncio.CancelledError):
        return frozenset()


async def _refresh_repo_branches(repo_root: str) -> frozenset[str]:
    """Refresh the repo-branches cache, deduplicating concurrent callers."""
    inflight = _repo_branches_inflight.get(repo_root)
    if inflight is not None:
        try:
            return await inflight
        except Exception:
            pass  # fall through and try again below
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _repo_branches_inflight[repo_root] = fut
    try:
        branches = await _list_repo_branches_async(repo_root)
        _repo_branches_cache[repo_root] = (
            time.monotonic() + _BRANCH_EXISTS_TTL_SECONDS, branches)
        if not fut.done():
            fut.set_result(branches)
        return branches
    except Exception as exc:
        if not fut.done():
            fut.set_exception(exc)
        raise
    finally:
        _repo_branches_inflight.pop(repo_root, None)


async def prefill_branch_exists_async(repo_roots: Iterable[str]) -> None:
    """Warm the per-repo branch cache for each distinct ``repo_root``.

    Uses one async `git for-each-ref` per repo, concurrently. Skips repos
    whose cache entry is still fresh. Safe to call from any async context.
    """
    now = time.monotonic()
    seen: set[str] = set()
    targets: list[str] = []
    for raw in repo_roots:
        repo_root = _normalize_repo_root(raw or "")
        if not repo_root or repo_root in seen:
            continue
        seen.add(repo_root)
        cached = _repo_branches_cache.get(repo_root)
        if cached is not None and cached[0] > now:
            continue
        targets.append(repo_root)
    if not targets:
        return
    await asyncio.gather(
        *(_refresh_repo_branches(repo_root) for repo_root in targets),
        return_exceptions=True,
    )


def _collect_state_repo_roots(state, *, group: str = "") -> set[str]:
    """Return distinct normalized repo_roots referenced by agents + tasks."""
    repo_roots: set[str] = set()
    for cell in state.iter_active_agents():
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if group and getattr(cell, "group", "") != group:
            continue
        repo_root = str(
            getattr(cell, "worktree_repo_root", "")
            or getattr(cell, "git_root", "")
            or ""
        ).strip()
        if repo_root:
            repo_roots.add(_normalize_repo_root(repo_root))
    if group and hasattr(state, "tasks_in_group"):
        tasks = state.tasks_in_group(group)
    else:
        tasks = state.board_tasks.values()
    for task in tasks:
        for attr in ("worktree_repo_root", "repo_root"):
            repo_root = str(getattr(task, attr, "") or "").strip()
            if repo_root:
                repo_roots.add(_normalize_repo_root(repo_root))
    repo_roots.discard("")
    return repo_roots


async def prefill_branch_exists_for_state(state, *, group: str = "",
                                          include_merge_readiness: bool = True
                                          ) -> None:
    """Warm branch and (by default) current-base merge-readiness caches."""
    repo_roots = _collect_state_repo_roots(state, group=group)
    if repo_roots:
        await prefill_branch_exists_async(repo_roots)
    if include_merge_readiness:
        await prefill_merge_readiness_for_state(state, group=group)


async def _run_merge_readiness_git(repo_root: str, *args: str) -> tuple[int, str]:
    """Run a bounded git probe used solely to populate merge readiness."""
    proc = None
    try:
        async with _merge_readiness_semaphore:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_MERGE_READINESS_GIT_TIMEOUT_SECONDS
            )
        return proc.returncode, stdout.decode("utf-8", errors="replace").strip()
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except (OSError, asyncio.TimeoutError):
                pass
        return 1, ""
    except OSError:
        return 1, ""


async def _probe_merge_readiness(repo_root: str, branch: str,
                                 base_branch: str) -> dict:
    """Check current-base mergeability using ``merge-tree --write-tree``.

    A moved base is recorded separately from a conflict.  The latter is the
    condition that makes a ready stream impossible to merge now.
    """
    repo_root, branch, base_branch = _merge_readiness_cache_key(
        repo_root, branch, base_branch
    )
    unknown = {
        "state": "unknown",
        "stale": None,
        "source": "merge_readiness_check_failed",
        "base_branch": base_branch,
    }
    if not all((repo_root, branch, base_branch)) or not os.path.isdir(repo_root):
        return unknown
    base_code, base_head = await _run_merge_readiness_git(
        repo_root, "rev-parse", base_branch
    )
    branch_code, branch_head = await _run_merge_readiness_git(
        repo_root, "rev-parse", branch
    )
    fork_code, fork_point = await _run_merge_readiness_git(
        repo_root, "merge-base", base_branch, branch
    )
    if base_code or branch_code or fork_code or not all(
            (base_head, branch_head, fork_point)):
        return unknown

    # Do not use the old three-argument merge-tree form: it can exit with a
    # confident-looking result that does not expose conflicts to the caller.
    merge_code, merge_output = await _run_merge_readiness_git(
        repo_root, "merge-tree", "--write-tree", "--name-only",
        base_branch, branch,
    )
    base_advanced = fork_point != base_head
    conflict = merge_code != 0
    return {
        "state": "stale" if base_advanced else "fresh",
        "stale": base_advanced,
        "source": "merge_readiness_check",
        "base_branch": base_branch,
        "base_head": base_head,
        "branch_head": branch_head,
        "fork_point": fork_point,
        "base_advanced": base_advanced,
        "merge_clean": not conflict,
        "merge_conflict": conflict,
        # This is deliberately only a diagnostic; conflict truth is the
        # merge-tree exit status, not a grep over its human output.
        "merge_tree_output": merge_output[:1000] if conflict else "",
    }


def _stream_base_branch(state, stream: dict) -> str:
    readiness = (stream or {}).get("merge_readiness", {}) or {}
    stale = readiness.get("stale_base", {}) if isinstance(readiness, dict) else {}
    base_branch = str((stale or {}).get("base_branch", "") or "").strip()
    if base_branch:
        return base_branch
    pr = (stream or {}).get("pr", {}) or {}
    base_branch = str(pr.get("base_branch", "") or "").strip()
    if base_branch:
        return base_branch
    owner = state.agents.get(str((stream or {}).get("agent_id", "") or ""))
    return str(getattr(owner, "worktree_base_branch", "") or "").strip()


async def prefill_merge_readiness_for_streams(state, streams) -> bool:
    """Warm current-base merge probes for precomputed ``streams``.

    Returns True when at least one probe actually ran (i.e. the cache held
    no fresh entry for some stream), so callers know a recompute would now
    observe different readiness data. Failure is intentionally cached as
    ``unknown`` and downstream synthesis treats it as not-ready rather than
    falsely recommending a merge.
    """
    probes = []
    for stream in streams or []:
        stream = stream or {}
        if str(stream.get("state", "") or "") == "merged":
            continue
        repo_root = str(stream.get("repo_root", "") or "").strip()
        branch = str(stream.get("branch", "") or "").strip()
        base_branch = _stream_base_branch(state, stream)
        if not all((repo_root, branch, base_branch)):
            continue
        if _merge_readiness_cache_get(repo_root, branch, base_branch):
            continue
        probes.append((repo_root, branch, base_branch))
    probes = list(dict.fromkeys(probes))

    async def populate(repo_root: str, branch: str, base_branch: str) -> None:
        info = await _probe_merge_readiness(repo_root, branch, base_branch)
        _merge_readiness_cache_put(repo_root, branch, base_branch, info)

    if probes:
        await asyncio.gather(
            *(populate(*probe) for probe in probes), return_exceptions=True,
        )
    return bool(probes)


async def prefill_merge_readiness_for_state(state, *, group: str = "") -> None:
    """Warm current-base merge probes before synchronous stream synthesis."""
    # Deferred to avoid the import cycle: worktree_streams re-exports this
    # module's readiness helpers.
    from .worktree_streams import compute_worktree_streams

    await prefill_branch_exists_for_state(
        state, group=group, include_merge_readiness=False,
    )
    streams = compute_worktree_streams(
        state, group=group, include_orphaned=False,
    )
    await prefill_merge_readiness_for_streams(state, streams)


def _branch_exists_locally(repo_root: str, branch: str, *,
                           cache: dict[tuple[str, str], bool] | None = None
                           ) -> bool:
    """Return True if ``branch`` exists as a local ref in ``repo_root``.

    Fast path: consult the batched per-repo branch set populated by
    ``prefill_branch_exists_async``. No subprocess is spawned when the
    cache is warm. Falls back to the legacy per-branch TTL cache and, as
    a last resort, a synchronous `git show-ref` — this only happens when
    a caller reaches this path without having run the async prefill
    first (primarily CLI / test contexts).
    """
    repo_root = _normalize_repo_root(repo_root)
    branch = str(branch or "").strip()
    if not repo_root or not branch:
        return False
    key = (repo_root, branch)
    if cache is not None and key in cache:
        return cache[key]

    now = time.monotonic()
    repo_entry = _repo_branches_cache.get(repo_root)
    if repo_entry is not None and repo_entry[0] > now:
        exists = branch in repo_entry[1]
        if cache is not None:
            cache[key] = exists
        return exists

    cached = _branch_exists_ttl_cache.get(key)
    if cached is not None and cached[0] > now:
        exists = cached[1]
        if cache is not None:
            cache[key] = exists
        return exists

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    if running_loop is not None:
        # We are blocking the event loop; a synchronous `git show-ref` fork
        # here used to stall the daemon for seconds when the cache was cold
        # (one fork per stream). Serve the freshest stale answer instead and
        # warm the cache in the background for the next synthesis. The stale
        # answer is not written back to the TTL cache so the refresh wins.
        running_loop.create_task(prefill_branch_exists_async([repo_root]))
        if repo_entry is not None:
            exists = branch in repo_entry[1]
        elif cached is not None:
            exists = cached[1]
        else:
            exists = False
        if cache is not None:
            cache[key] = exists
        return exists

    exists = False
    if os.path.isdir(repo_root):
        try:
            proc = subprocess.run(
                [
                    "git", "-C", repo_root, "show-ref", "--verify", "--quiet",
                    f"refs/heads/{branch}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=_BRANCH_EXISTS_GIT_TIMEOUT_SECONDS,
            )
            exists = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            exists = False

    _branch_exists_ttl_cache[key] = (now + _BRANCH_EXISTS_TTL_SECONDS, exists)
    if cache is not None:
        cache[key] = exists
    return exists
