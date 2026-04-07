"""Shared context memory helpers for Loom.

This module intentionally keeps v1 simple:
- explicit, durable entries only
- deterministic scope resolution
- deterministic list ordering/filtering
- no implicit auto-memory or semantic search
"""

from __future__ import annotations

import time
import uuid


ENTRY_TYPES = ("finding", "decision", "warning", "handoff", "note")
SCOPE_KINDS = ("task", "pipeline", "group", "project")

MAX_TITLE_LEN = 200
MAX_CONTENT_LEN = 4000


def normalize_entry_type(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in ENTRY_TYPES:
        raise ValueError(
            f"Invalid entry type '{value}'. "
            f"Expected one of: {', '.join(ENTRY_TYPES)}"
        )
    return value


def normalize_scope_kind(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in SCOPE_KINDS:
        raise ValueError(
            f"Invalid scope kind '{value}'. "
            f"Expected one of: {', '.join(SCOPE_KINDS)}"
        )
    return value


def clamp_text(value: str, max_len: int) -> str:
    value = (value or "").strip()
    if len(value) > max_len:
        value = value[:max_len].rstrip()
    return value


def detect_current_task(state, cell_id: str, explicit_task_id: str = ""):
    """Resolve the task currently associated with *cell_id*.

    Mirrors the existing ai_report resolution strategy closely enough for
    memory publishing/listing defaults.
    """
    if explicit_task_id:
        return state.board_tasks.get(explicit_task_id)

    cell = state.agents.get(cell_id)
    if not cell:
        return None

    if cell.current_task_id:
        task = state.board_tasks.get(cell.current_task_id)
        if task:
            return task

    linked = [
        t for t in state.board_tasks.values()
        if t.agent_id == cell_id and t.lane not in ("Done", "Backlog")
    ]
    if len(linked) == 1:
        return linked[0]
    return None


def infer_project_key(cell=None, task=None) -> str:
    """Best-effort project key derived from the active worktree/repo root."""
    if cell:
        for key in ("worktree_repo_root", "git_root", "directory"):
            val = getattr(cell, key, "") or ""
            if val:
                return val
    return ""


def resolve_scope(state, cell=None, task=None, *,
                  scope_kind: str = "", scope_ref: str = "") -> tuple[str, str]:
    """Resolve scope defaults from the current agent/task context."""
    if scope_kind:
        kind = normalize_scope_kind(scope_kind)
    elif task:
        kind = "task"
    elif cell and getattr(cell, "group", ""):
        kind = "group"
    else:
        raise ValueError("Cannot infer scope without an active task or group")

    ref = (scope_ref or "").strip()
    if kind == "task":
        if ref:
            return kind, ref
        if not task:
            raise ValueError("Task scope requires an active task or scope_ref")
        return kind, task.id

    if kind == "pipeline":
        if ref:
            return kind, ref
        if not task:
            raise ValueError(
                "Pipeline scope requires an active task or scope_ref"
            )
        return kind, task.pipeline_root_id or task.id

    if kind == "group":
        if ref:
            return kind, ref
        group = ""
        if task:
            group = task.group or ""
        if not group and cell:
            group = getattr(cell, "group", "") or ""
        if not group:
            raise ValueError("Group scope requires a group or scope_ref")
        return kind, group

    if ref:
        return kind, ref
    project_key = infer_project_key(cell=cell, task=task)
    if not project_key:
        raise ValueError(
            "Project scope requires a repo/worktree context or explicit scope_ref"
        )
    return kind, project_key


def build_memory_entry(state, *, cell=None, task=None,
                       entry_type: str, content: str, title: str = "",
                       scope_kind: str = "", scope_ref: str = "",
                       pinned: bool = False, source_kind: str = "agent") -> dict:
    """Build a normalized memory entry record ready for persistence."""
    kind, ref = resolve_scope(
        state, cell=cell, task=task, scope_kind=scope_kind, scope_ref=scope_ref
    )
    if not task and kind == "task":
        task = state.board_tasks.get(ref)
    elif not task and kind == "pipeline":
        for candidate in state.board_tasks.values():
            if (candidate.pipeline_root_id or candidate.id) == ref:
                task = candidate
                break
    now = time.time()

    content = clamp_text(content, MAX_CONTENT_LEN)
    if not content:
        raise ValueError("Memory content cannot be empty")

    title = clamp_text(title, MAX_TITLE_LEN)
    cell_id = getattr(cell, "id", "") if cell else ""
    cell_name = getattr(cell, "name", "") if cell else ""
    group_name = ""
    if task:
        group_name = task.group or ""
    if not group_name and cell:
        group_name = getattr(cell, "group", "") or ""
    if not group_name and kind == "group":
        group_name = ref
    project_key = infer_project_key(cell=cell, task=task)
    if not project_key and task and getattr(task, "agent_id", ""):
        project_key = infer_project_key(cell=state.agents.get(task.agent_id))
    if not project_key and kind == "project":
        project_key = ref

    return {
        "id": uuid.uuid4().hex[:12],
        "project_key": project_key,
        "group_name": group_name,
        "scope_kind": kind,
        "scope_ref": ref,
        "entry_type": normalize_entry_type(entry_type),
        "title": title,
        "content": content,
        "pinned": bool(pinned),
        "task_id": getattr(task, "id", "") if task else "",
        "source_kind": source_kind or "agent",
        "source_id": cell_id,
        "source_name": cell_name,
        "created_at": now,
        "updated_at": now,
    }
