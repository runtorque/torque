"""Board dependency, review-handoff, and task query behavior."""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from .state import (
    ARCHIVED_LANE,
    BoardTask,
    board_task_counts_as_done,
    board_task_is_closed,
    task_counts_as_done,
    task_suppresses_done_cascade,
)


class BoardQueryMixin:
    def board_get_children(self, task_id: str) -> list[BoardTask]:
        """Return direct children of a task (derived tasks)."""
        task_id = self.resolve_task_alias(task_id)
        return [t for t in self.board_tasks.values()
                if t.parent_task_id == task_id]

    def _board_check_dep_cycle(self, source_id: str,
                               new_deps: list[str]) -> bool:
        """DFS: True if adding new_deps to source_id creates a cycle."""
        visited = set()
        stack = list(new_deps)
        while stack:
            tid = stack.pop()
            if tid == source_id:
                return True
            if tid in visited:
                continue
            visited.add(tid)
            t = self.board_tasks.get(tid)
            if t:
                stack.extend(t.depends_on)
        return False

    def board_deps_met(self, task: BoardTask) -> bool:
        """True if all depends_on tasks are Done (or deleted)."""
        for dep_id in task.depends_on:
            dep = self.board_tasks.get(dep_id)
            if dep and not board_task_counts_as_done(dep):
                return False
        return True

    def board_get_dependents(self, task_id: str) -> list[BoardTask]:
        """Tasks that have task_id in their depends_on."""
        task_id = self.resolve_task_alias(task_id)
        return [t for t in self.board_tasks.values()
                if task_id in t.depends_on]

    def board_get_chain(self, task_id: str) -> list[BoardTask]:
        """Return all tasks in the same pipeline chain, ordered by depth."""
        task_id = self.resolve_task_alias(task_id)
        task = self.board_tasks.get(task_id)
        if not task:
            return []
        root_id = task.pipeline_root_id or task.id
        chain = [t for t in self.board_tasks.values()
                 if (t.pipeline_root_id == root_id) or (t.id == root_id)]
        chain.sort(key=lambda t: (t.pipeline_depth, t.created_at))
        return chain

    @staticmethod
    def _task_action_name(task: Optional[BoardTask]) -> str:
        return str(getattr(task, "action_name", "") or "").strip().lower()

    def _is_review_handoff_source(self, task: Optional[BoardTask]) -> bool:
        return self._task_action_name(task) == "feature/review"

    def _is_review_handoff_followup(self, review: Optional[BoardTask],
                                    task: Optional[BoardTask]) -> bool:
        """Return whether ``task`` is the implementation follow-up for review.

        TORQUE:88 review-derived fixes are structurally parented to the reviewed
        implementation task (the review's parent) so dispatch inherits the
        implementer's worktree instead of the reviewer's branch.  They still
        behave like logical handoff descendants of the review for execution
        slots and completion cascades.
        """
        if not review or not task or review.id == task.id:
            return False
        if not self._is_review_handoff_source(review):
            return False
        if self._task_action_name(task) not in {
            "feature/implement",
            "feature/fix-review",
        }:
            return False
        review_parent_id = str(
            getattr(review, "parent_task_id", "") or ""
        ).strip()
        if not review_parent_id:
            return False
        task_parent_id = str(
            getattr(task, "parent_task_id", "") or ""
        ).strip()
        if task_parent_id != review_parent_id:
            return False
        review_root_id = str(
            getattr(review, "pipeline_root_id", "") or review.id
        ).strip()
        task_root_id = str(
            getattr(task, "pipeline_root_id", "") or task.id
        ).strip()
        if review_root_id != task_root_id:
            return False
        if int(getattr(task, "pipeline_depth", 0) or 0) <= int(
                getattr(review, "pipeline_depth", 0) or 0):
            return False
        review_created = str(getattr(review, "created_at", "") or "")
        task_created = str(getattr(task, "created_at", "") or "")
        if review_created and task_created and task_created < review_created:
            return False
        return True

    def review_handoff_followups(self, review_id: str) -> list[BoardTask]:
        review_id = self.resolve_task_alias(review_id)
        review = self.board_tasks.get(review_id)
        if not self._is_review_handoff_source(review):
            return []
        followups = [
            task for task in self.board_tasks.values()
            if self._is_review_handoff_followup(review, task)
        ]
        followups.sort(key=lambda t: (t.pipeline_depth, t.created_at, t.id))
        return followups

    def _review_handoff_reviews_for_followup(
            self, task: Optional[BoardTask]) -> list[BoardTask]:
        if not task:
            return []
        reviews = [
            review for review in self.board_tasks.values()
            if self._is_review_handoff_followup(review, task)
        ]
        reviews.sort(key=lambda t: (t.pipeline_depth, t.created_at, t.id))
        return reviews

    def _cascade_review_handoff_completions(
            self, task: Optional[BoardTask],
            changed: list[str]) -> None:
        """Complete review tasks whose sibling fix handoff is fully resolved."""
        if not task or not task_counts_as_done(task):
            return
        if self.task_has_unresolved_descendants(task.id):
            return
        for review in self._review_handoff_reviews_for_followup(task):
            if task_suppresses_done_cascade(review):
                continue
            if board_task_is_closed(review):
                continue
            if self.task_has_unresolved_descendants(review.id):
                continue
            if self._is_finalization_root(review):
                allowed, _result = self._finalization_done_allowed(
                    review, caller="cascade_review_handoff_completions"
                )
                if not allowed:
                    continue
            review.status = ""
            self._board_apply_archive_state(
                review,
                lane="Done",
                archived_at="",
                archived_from_lane="",
                clear_attention=True,
            )
            if review.id not in changed:
                changed.append(review.id)

    def task_open_descendants(self, task_id: str) -> list[BoardTask]:
        """Return all unresolved descendants for ``task_id``."""
        task_id = self.resolve_task_alias(task_id)
        if not task_id:
            return []
        descendants = []
        stack = self.board_get_children(task_id)
        stack.extend(self.review_handoff_followups(task_id))
        seen = set()
        while stack:
            current = stack.pop()
            if current.id in seen:
                continue
            seen.add(current.id)
            stack.extend(self.board_get_children(current.id))
            if self._is_review_handoff_source(current):
                stack.extend(self.review_handoff_followups(current.id))
            if task_counts_as_done(current):
                continue
            descendants.append(current)
        descendants.sort(key=lambda t: (t.pipeline_depth, t.created_at, t.id))
        return descendants

    def task_has_unresolved_descendants(self, task_id: str) -> bool:
        """Return whether any descendant branch is still unresolved."""
        return bool(self.task_open_descendants(task_id))

    def task_has_live_handoff_descendants(self, task_id: str) -> bool:
        """Return whether work has been handed off beyond this task.

        A descendant counts as a live handoff once it is clearly on another
        execution path: queued, started, assigned to an agent, or awaiting a
        human reply. Plain non-human Backlog children do *not* count; those can
        represent a derive that created a task before dispatch actually
        succeeded, and should not free the current agent slot yet.
        """
        for descendant in self.task_open_descendants(task_id):
            labels = set(descendant.labels or [])
            if descendant.lane in {"To Do", "In Progress"}:
                return True
            if descendant.agent_id:
                return True
            if "torque:human" in labels:
                return True
        return False

    def task_occupies_execution_slot(self, task: Optional[BoardTask], *,
                                     agent_id: str = "") -> bool:
        """Return whether ``task`` still occupies an agent's live slot."""
        if not task or board_task_is_closed(task):
            return False
        if agent_id and task.agent_id != agent_id:
            return False
        if task.lane in {"Backlog", "Done", ARCHIVED_LANE}:
            return False
        if self.task_has_live_handoff_descendants(task.id):
            return False
        return True

    def agent_active_tasks(self, agent_id: str) -> list[BoardTask]:
        tasks = [
            t for t in self.board_tasks.values()
            if self.task_occupies_execution_slot(t, agent_id=agent_id)
        ]
        tasks.sort(
            key=lambda t: (t.lane != "In Progress", t.position,
                           t.created_at, t.id)
        )
        return tasks

    def agent_current_task(self, agent_id: str) -> Optional[BoardTask]:
        cell = self.agents.get(agent_id)
        if cell and cell.current_task_id:
            task = self.board_tasks.get(cell.current_task_id)
            if self.task_occupies_execution_slot(task, agent_id=agent_id):
                return task
        tasks = self.agent_active_tasks(agent_id)
        return tasks[0] if tasks else None

    def agent_pending_engineer_reply_tasks(self, agent_id: str) -> list[BoardTask]:
        """Return open Engineer follow-up tasks awaiting ``agent_id`` replies."""
        tasks = [
            task for task in self.board_tasks.values()
            if task.reply_agent_id == agent_id and not board_task_is_closed(task)
        ]
        tasks.sort(key=lambda task: (task.created_at, task.id))
        return tasks

    def agent_is_busy(self, agent_id: str) -> bool:
        cell = self.agents.get(agent_id)
        if cell and cell.current_task_id:
            task = self.board_tasks.get(cell.current_task_id)
            if task and self.task_occupies_execution_slot(
                    task, agent_id=agent_id):
                return True
        return self.agent_current_task(agent_id) is not None

    def extract_playbook_candidates(self, group: str = "") -> list[dict]:
        """Mine and persist draft playbook candidates from task history."""
        if not self.db:
            return []
        from .playbooks import extract_playbook_candidates

        candidates = extract_playbook_candidates(self, group=group)
        self.db.replace_playbook_candidates(candidates, group_name=group)
        return candidates

    def list_playbook_candidates(self, group: str = "",
                                 limit: int = 50) -> list[dict]:
        """Load persisted draft playbook candidates."""
        if not self.db:
            return []
        return self.db.load_playbook_candidates(group_name=group, limit=limit)

    def save_playbook(self, playbook: dict):
        """Persist a generated draft or published playbook."""
        if not self.db:
            return
        self.db.save_playbook(playbook)

    def list_playbooks(self, group: str = "", status: str = "",
                       limit: int = 50) -> list[dict]:
        """Load persisted playbook drafts or published recipes."""
        if not self.db:
            return []
        return self.db.load_playbooks(
            group_name=group, status_filter=status, limit=limit)

    def get_playbook(self, playbook_id: str) -> Optional[dict]:
        """Load one persisted playbook draft or published recipe."""
        if not self.db:
            return None
        return self.db.load_playbook(playbook_id)

    def board_unlink_agent(self, agent_id: str):
        """Unlink an agent from all tasks (called when agent is removed)."""
        changed = False
        for t in self.board_tasks.values():
            if t.agent_id == agent_id:
                t.agent_id = ""
                self._emit("task_upsert", **asdict(t))
                self._db_save_task(t)
                changed = True
        if changed:
            self.recompute_task_health()
