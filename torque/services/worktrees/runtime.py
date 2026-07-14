"""Explicit legacy dependencies for worktree orchestration services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WorktreeOrchestrationDependencies:
    cleanup_shipped_reviewers_for_merged_cell: Any
    origin_verification_evidence: Any
    record_merge_completion_evidence: Any
    resolve_agent_id: Any
    worktree_entry_matches_agent: Any
    worktree_path_contains: Any


_dependencies: WorktreeOrchestrationDependencies | None = None


def configure_worktree_orchestration(
    dependencies: WorktreeOrchestrationDependencies,
) -> None:
    global _dependencies
    _dependencies = dependencies


def _dependency(name: str):
    if _dependencies is None:
        raise RuntimeError("worktree orchestration dependencies are not configured")
    return getattr(_dependencies, name)


async def _cleanup_shipped_reviewers_for_merged_cell(*args, **kwargs):
    return await _dependency("cleanup_shipped_reviewers_for_merged_cell")(
        *args, **kwargs
    )


def _origin_verification_evidence(*args, **kwargs):
    return _dependency("origin_verification_evidence")(*args, **kwargs)


def _record_merge_completion_evidence(*args, **kwargs):
    return _dependency("record_merge_completion_evidence")(*args, **kwargs)


def _resolve_agent_id(*args, **kwargs):
    return _dependency("resolve_agent_id")(*args, **kwargs)


def _worktree_entry_matches_agent(*args, **kwargs):
    return _dependency("worktree_entry_matches_agent")(*args, **kwargs)


def _worktree_path_contains(*args, **kwargs):
    return _dependency("worktree_path_contains")(*args, **kwargs)
