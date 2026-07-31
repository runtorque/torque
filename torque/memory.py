"""Shared context memory helpers for Torque.

Shared context is explicit, deterministic, and deliberately time-bounded.
Every new entry expires according to its group's configured TTL.
"""

from __future__ import annotations

import time
import uuid


ENTRY_TYPES = ("finding", "decision", "warning", "handoff", "note")
SCOPE_KINDS = ("task", "pipeline", "group", "project")
LINK_TARGET_KINDS = ("task", "agent", "pipeline")
# The maximum is a system policy, not a configurable setting.
CONTEXT_TTL_MIN_DAYS = 1
CONTEXT_TTL_MAX_DAYS = 60
CONTEXT_DEFAULT_TTL_DAYS = 30
CONTEXT_TTL_SECS_PER_DAY = 24 * 60 * 60
DEPRECATED_RETENTION_KIND = "ttl"

PROMPT_QUERY_LIMIT = 8
PROMPT_MAX_ENTRIES = 4
PROMPT_MAX_CHARS = 1200
PROMPT_ITEM_MAX_CHARS = 220

_PROMPT_REASON_WEIGHTS = {
    "scope_task": 4000,
    "link_task": 3400,
    "scope_pipeline": 3000,
    "link_pipeline": 2600,
    "link_agent": 2200,
    "scope_group": 1200,
    "scope_project": 500,
}
_PROMPT_REASON_LABELS = {
    "scope_task": "task",
    "link_task": "task-link",
    "scope_pipeline": "pipeline",
    "link_pipeline": "pipeline-link",
    "link_agent": "agent-link",
    "scope_group": "group",
    "scope_project": "project",
}

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


def normalize_link_target_kind(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in LINK_TARGET_KINDS:
        raise ValueError(
            f"Invalid link target kind '{value}'. "
            f"Expected one of: {', '.join(LINK_TARGET_KINDS)}"
        )
    return value


def clamp_context_ttl_days(value) -> int:
    """Coerce a group context TTL into the supported policy range."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = CONTEXT_DEFAULT_TTL_DAYS
    return max(CONTEXT_TTL_MIN_DAYS, min(CONTEXT_TTL_MAX_DAYS, days))


def context_ttl_days_for_group(state, group_name: str) -> int:
    """Return the configured, policy-clamped TTL for a group's context."""
    settings = state.get_group_settings(group_name) if state else None
    return clamp_context_ttl_days(getattr(
        settings, "context_default_ttl_days", CONTEXT_DEFAULT_TTL_DAYS,
    ))


def expires_at_for_ttl(*, created_at: float, ttl_days: int) -> float:
    """Return the expiry timestamp for a newly created memory entry."""
    return float(created_at) + clamp_context_ttl_days(ttl_days) * CONTEXT_TTL_SECS_PER_DAY

def clamp_text(value: str, max_len: int) -> str:
    value = (value or "").strip()
    if len(value) > max_len:
        value = value[:max_len].rstrip()
    return value



def is_entry_expired(entry: dict, *, now: float | None = None) -> bool:
    """Return True when an entry's stored expiry has elapsed."""
    expiry = entry.get("expires_at")
    if expiry in (None, "", 0):
        return False
    if now is None:
        now = time.time()
    return float(expiry) <= float(now)


def load_visible_memory_entries(db, *, now: float | None = None,
                                **filters) -> list[dict]:
    """Load non-expired entries from storage without synthetic compaction."""
    return db.load_memory_entries(now=now, **filters)

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
                       pinned: bool = False, source_kind: str = "agent",
                       retention_kind: str = "") -> dict:
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
    entry_type = normalize_entry_type(entry_type)

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
    # ``retention_kind`` remains an accepted legacy tool argument, but does
    # not influence lifetime. Pinning is likewise visibility-only.
    del retention_kind
    expires_at = expires_at_for_ttl(
        created_at=now,
        ttl_days=context_ttl_days_for_group(state, group_name),
    )

    return {
        "id": uuid.uuid4().hex[:12],
        "project_key": project_key,
        "group_name": group_name,
        "scope_kind": kind,
        "scope_ref": ref,
        "entry_type": entry_type,
        "title": title,
        "content": content,
        "pinned": bool(pinned),
        "task_id": getattr(task, "id", "") if task else "",
        "source_kind": source_kind or "agent",
        "source_id": cell_id,
        "source_name": cell_name,
        # Kept in read payloads for panel compatibility while the deprecated
        # storage column exists; it no longer encodes a retention class.
        "retention_kind": DEPRECATED_RETENTION_KIND,
        "expires_at": expires_at,
        "created_at": now,
        "updated_at": now,
    }


def build_memory_link(state, *, entry_id: str, target_kind: str,
                      target_ref: str = "", cell=None, task=None) -> dict:
    """Build a normalized memory-link record ready for persistence."""
    kind = normalize_link_target_kind(target_kind)
    ref = (target_ref or "").strip()

    if kind == "task":
        if not ref:
            if not task:
                raise ValueError("Task link requires an active task or target_ref")
            ref = task.id
    elif kind == "agent":
        if not ref:
            if not cell:
                raise ValueError(
                    "Agent link requires an active agent or target_ref"
                )
            ref = getattr(cell, "id", "") or ""
        if not ref:
            raise ValueError("Agent link requires a target_ref")
    else:
        if not ref:
            if not task:
                raise ValueError(
                    "Pipeline link requires an active task or target_ref"
                )
            ref = task.pipeline_root_id or task.id
        if not ref:
            raise ValueError("Pipeline link requires a target_ref")

    return {
        "entry_id": entry_id,
        "target_kind": kind,
        "target_ref": ref,
        "created_at": time.time(),
    }


def select_relevant_prompt_entries(db, *, cell=None, task=None,
                                   query_limit: int = PROMPT_QUERY_LIMIT,
                                   max_entries: int = PROMPT_MAX_ENTRIES
                                   ) -> list[dict]:
    """Return top shared-memory entries for prompt injection.

    Ordering is deterministic:
    - pinned entries first
    - nearest matching scope/link next
    - newer entries ahead of older ones
    """
    if not db or (not cell and not task):
        return []

    pipeline_ref = ""
    if task:
        pipeline_ref = task.pipeline_root_id or task.id

    group_ref = ""
    if task and getattr(task, "group", ""):
        group_ref = task.group
    elif cell and getattr(cell, "group", ""):
        group_ref = cell.group

    project_ref = infer_project_key(cell=cell, task=task)
    agent_ref = getattr(cell, "id", "") if cell else ""

    queries = []
    if task and getattr(task, "id", ""):
        queries.append((
            "scope_task",
            {"scope_kind": "task", "scope_ref": task.id},
        ))
        queries.append((
            "link_task",
            {"linked_target_kind": "task", "linked_target_ref": task.id},
        ))
    if pipeline_ref:
        queries.append((
            "scope_pipeline",
            {"scope_kind": "pipeline", "scope_ref": pipeline_ref},
        ))
        queries.append((
            "link_pipeline",
            {
                "linked_target_kind": "pipeline",
                "linked_target_ref": pipeline_ref,
            },
        ))
    if agent_ref:
        queries.append((
            "link_agent",
            {"linked_target_kind": "agent", "linked_target_ref": agent_ref},
        ))
    if group_ref:
        queries.append((
            "scope_group",
            {"scope_kind": "group", "scope_ref": group_ref},
        ))
    if project_ref:
        queries.append((
            "scope_project",
            {
                "scope_kind": "project",
                "scope_ref": project_ref,
                "project_key": project_ref,
            },
        ))

    candidates = {}
    for reason, filters in queries:
        entries = load_visible_memory_entries(
            db,
            limit=max(1, int(query_limit)),
            **filters,
        )
        for rank, entry in enumerate(entries):
            current = candidates.setdefault(
                entry["id"],
                {
                    "entry": dict(entry),
                    "reasons": set(),
                    "best_rank": rank,
                },
            )
            current["reasons"].add(reason)
            current["best_rank"] = min(current["best_rank"], rank)

    ranked = []
    for item in candidates.values():
        entry = item["entry"]
        score = sum(_PROMPT_REASON_WEIGHTS[r] for r in item["reasons"])
        if entry.get("pinned"):
            score += 10000
        score += max(0, 50 - item["best_rank"])
        ranked.append({
            "entry": entry,
            "score": score,
            "match_reasons": sorted(
                item["reasons"],
                key=lambda reason: (
                    -_PROMPT_REASON_WEIGHTS[reason],
                    reason,
                ),
            ),
        })

    ranked.sort(
        key=lambda item: (
            -item["score"],
            -float(item["entry"].get("created_at", 0)),
            item["entry"]["id"],
        )
    )
    return ranked[:max(1, int(max_entries))]


def build_prompt_memory_block(db, *, cell=None, task=None,
                              query_limit: int = PROMPT_QUERY_LIMIT,
                              max_entries: int = PROMPT_MAX_ENTRIES,
                              max_chars: int = PROMPT_MAX_CHARS) -> str:
    """Render a bounded shared-context block for task prompts."""
    selected = select_relevant_prompt_entries(
        db,
        cell=cell,
        task=task,
        query_limit=query_limit,
        max_entries=max_entries,
    )
    if not selected:
        return ""

    header = "\n\nRelevant shared context (pinned + nearest scope first):"
    lines = []
    total_len = len(header)

    for item in selected:
        entry = item["entry"]
        tags = []
        if entry.get("pinned"):
            tags.append("pinned")
        for reason in item["match_reasons"]:
            label = _PROMPT_REASON_LABELS[reason]
            if label not in tags:
                tags.append(label)

        title = clamp_text(entry.get("title", ""), 80)
        content = clamp_text(entry.get("content", ""), PROMPT_ITEM_MAX_CHARS)
        if title and title != content:
            body = f"{title} — {content}"
        else:
            body = content
        body = clamp_text(body, PROMPT_ITEM_MAX_CHARS)

        tag_text = entry.get("entry_type", "")
        if tags:
            tag_text += " • " + ", ".join(tags[:3])
        line = f"\n- [{tag_text}] {body}"

        remaining = max_chars - total_len
        if remaining <= 0:
            break
        if len(line) > remaining:
            min_body = clamp_text(body, max(20, remaining - len(f"\n- [{tag_text}] ")))
            line = f"\n- [{tag_text}] {min_body}"
            if len(line) > remaining:
                break
        lines.append(line)
        total_len += len(line)

    if not lines:
        return ""
    return header + "".join(lines)
