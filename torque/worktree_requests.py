"""Acknowledged worktree mutation scope and compatibility coordinator names."""

from .pending_requests import (
    CommandRequestConflict as WorktreeRequestConflict,
    PendingCommandWrites as PendingWorktreeWrites,
)

ACKNOWLEDGED_WORKTREE_MUTATIONS = frozenset({
    "worktree_create", "worktree_checkpoint", "worktree_rollback", "worktree_rebase",
    "worktree_remove", "worktree_create_pr", "worktree_merge",
})
