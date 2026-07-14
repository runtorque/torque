"""Git worktree lifecycle facade for Torque agents.

Implementation is composed from domain mixins in :mod:`torque.worktree_manager`.
"""

from .worktree_manager.support import *  # noqa: F403
from .worktree_manager.refresh import RefreshMixin
from .worktree_manager.nested_lifecycle import NestedLifecycleMixin
from .worktree_manager.nested_merge import NestedMergeMixin
from .worktree_manager.lifecycle import LifecycleMixin
from .worktree_manager.changes import ChangesMixin
from .worktree_manager.merge import MergeMixin
from .worktree_manager.github import GithubMixin


class WorktreeManager(
    RefreshMixin,
    NestedLifecycleMixin,
    NestedMergeMixin,
    LifecycleMixin,
    ChangesMixin,
    MergeMixin,
    GithubMixin,
):
    """Composition root for worktree lifecycle and Git/GitHub operations."""

    def __init__(self, *,
                 refresh_git_timeout_seconds: float =
                 WORKTREE_REFRESH_GIT_TIMEOUT_SECONDS,
                 refresh_max_concurrent: int =
                 WORKTREE_REFRESH_MAX_CONCURRENT):
        # Per-cell ephemeral fingerprint of (worktree_index_mtime,
        # base_ref_mtime). Used by `refresh_state` to skip the entire
        # status/diff/ahead-behind/is_merged probe when neither side has
        # advanced since the last tick.
        self._refresh_fingerprints: dict[str, tuple[float, float]] = {}
        self.refresh_git_timeout_seconds = max(
            0.1,
            float(refresh_git_timeout_seconds or
                  WORKTREE_REFRESH_GIT_TIMEOUT_SECONDS),
        )
        self._refresh_semaphore = asyncio.Semaphore(
            max(1, int(refresh_max_concurrent or WORKTREE_REFRESH_MAX_CONCURRENT))
        )
        self._refresh_inflight: dict[str, asyncio.Task] = {}
        self._refresh_metrics: dict[str, float | int | str] = {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "timeouts": 0,
            "missing_worktrees": 0,
            "coalesced": 0,
            "skipped_unchanged": 0,
            "examined": 0,
            "last_duration_ms": 0.0,
            "max_duration_ms": 0.0,
            "last_error_kind": "",
            "last_error_cell": "",
            "last_error_command": "",
            "active": 0,
            "max_concurrent": max(
                1,
                int(refresh_max_concurrent or WORKTREE_REFRESH_MAX_CONCURRENT),
            ),
        }
        self._refresh_issue_log_at: dict[tuple[str, str], float] = {}
