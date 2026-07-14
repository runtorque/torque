"""Agent prompt assembly, Torque context, and memory-scope resolution."""

from __future__ import annotations

from .agent_classes import agent_class_context_for_cell
from .artifacts import task_artifacts
from .identity import agent_identity_anchor, agent_kind_for_identity
from .memory import detect_current_task, infer_project_key
from .server_artifacts import serialize_upstream_task_artifacts
from .state import MatrixState


def _agent_kind_for_context(cell) -> str:
    return agent_kind_for_identity(cell)


def _agent_is_worker_for_role_preamble(cell) -> bool:
    if not cell or getattr(cell, "cell_type", "agent") != "agent":
        return False
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind == "worker":
        return True
    if kind in {"engineer", "architect", "terminal"}:
        return False
    return bool(str(getattr(cell, "created_by_engineer_id", "") or "").strip())


def _agent_role_slug(cell) -> str:
    return str(
        getattr(cell, "role", "")
        or getattr(cell, "template", "")
        or ""
    ).strip()


def _agent_owner_engineer_name(state: MatrixState, cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if not owner_id:
        owner_id = str(getattr(cell, "created_by_engineer_id", "") or "").strip()
    if not owner_id:
        return ""
    owner = state.agents.get(owner_id)
    return owner.name if owner else ""


def _owner_is_user_from_ids(*, owner_engineer_id: str = "",
                            created_by_engineer_id: str = "",
                            hired_by_architect_id: str = "") -> bool:
    """Return True when an agent's owner is the user.

    Owner-is-user is the absence of any non-user ownership stamp: no
    owning/creating engineer and no hiring architect. Expressed via the
    ownership ids (per the kinds-refactor invariants) rather than by
    hardcoding agent kinds, so it holds uniformly for user-owned
    architects, engineers, and workers.
    """
    return not (
        str(owner_engineer_id or "").strip()
        or str(created_by_engineer_id or "").strip()
        or str(hired_by_architect_id or "").strip()
    )


def _agent_owner_is_user(cell) -> bool:
    """Return True when ``cell``'s owner is the user (not engineer/architect)."""
    if cell is None:
        return False
    return _owner_is_user_from_ids(
        owner_engineer_id=getattr(cell, "owner_engineer_id", ""),
        created_by_engineer_id=getattr(cell, "created_by_engineer_id", ""),
        hired_by_architect_id=getattr(cell, "hired_by_architect_id", ""),
    )


def _normalize_prompt_block(text: str) -> str:
    return str(text or "").strip("\n")


def _assemble_worker_prompt(*, role_mgr, cell, base_dir: str = "",
                            prompt_body: str = "", postscript: str = "",
                            behavior_overlay_block: str = "",
                            disable_role_preamble: bool = False,
                            include_identity_anchor: bool = True) -> str:
    """Assemble the final worker prompt with optional role preamble.

    The final shape is:
    {identity anchor block}

    {role preamble block}

    {role-scoped Dynamic Behavior block}

    {task/action prompt block}

    {torque postscript}

    Empty blocks are omitted. Exactly one blank line is inserted between
    included blocks, and the final prompt always ends with a trailing newline.
    """
    blocks = []

    if include_identity_anchor:
        identity_anchor = agent_identity_anchor(cell)
        if identity_anchor:
            blocks.append(identity_anchor)

    if role_mgr and cell and not disable_role_preamble \
            and _agent_is_worker_for_role_preamble(cell):
        role_slug = _agent_role_slug(cell)
        if role_slug:
            role = role_mgr.load_role(role_slug, base_dir=base_dir) or {}
            preamble = role_mgr.render_preamble(role)
            if preamble:
                blocks.append(_normalize_prompt_block(preamble))

    overlay_block = _normalize_prompt_block(behavior_overlay_block)
    if overlay_block:
        blocks.append(overlay_block)

    body_block = _normalize_prompt_block(prompt_body)
    if body_block:
        blocks.append(body_block)

    postscript_block = _normalize_prompt_block(postscript)
    if postscript_block:
        blocks.append(postscript_block)

    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def _build_torque_context(state: MatrixState, cell, task) -> dict:
    """Build the ``torque`` namespace dict for Jinja2 template rendering."""
    workerish = _agent_is_worker_for_role_preamble(cell)
    cell_id = getattr(cell, "id", "")
    tasks_dispatched = int(getattr(cell, "tasks_dispatched", 0) or 0)
    worktree_path = getattr(cell, "worktree_path", "") or ""
    worktree_branch = getattr(cell, "worktree_branch", "") or ""
    worktree_base_branch = getattr(cell, "worktree_base_branch", "") or ""
    worktree_dirty = bool(getattr(cell, "worktree_dirty", False))
    worktree_diff = getattr(cell, "worktree_diff", {}) or {}
    worktree_checkpoints = int(
        getattr(cell, "worktree_checkpoints", 0) or 0
    )
    agent_ctx = {
        "id": cell_id,
        "name": getattr(cell, "name", ""),
        "slug": getattr(cell, "slug", ""),
        "type": getattr(cell, "agent_type", ""),
        "group": getattr(cell, "group", ""),
        "directory": getattr(cell, "directory", ""),
        "kind": _agent_kind_for_context(cell),
        "role": _agent_role_slug(cell) if workerish else "",
        "owner_engineer": _agent_owner_engineer_name(
            state, cell) if workerish else "",
        "agent_class": agent_class_context_for_cell(cell),
    }

    linked = sorted(
        (t for t in state.board_tasks.values()
         if t.agent_id == cell_id and t.id != getattr(task, "id", "")),
        key=lambda t: t.created_at,
    )
    context_ctx = {
        "is_clean": tasks_dispatched == 0,
        "tasks_dispatched": tasks_dispatched,
        "previous_tasks": [
            {"task": t.task, "lane": t.lane, "action": t.action_name}
            for t in linked
        ],
    }

    worktree_ctx = {
        "active": bool(worktree_path),
        "path": worktree_path,
        "branch": worktree_branch,
        "base_branch": worktree_base_branch,
        "dirty": worktree_dirty,
        "diff": worktree_diff,
        "checkpoints": worktree_checkpoints,
    }

    parent_agent_slug = ""
    parent_agent_name = ""
    parent_agent_id = ""
    parent_task_id = getattr(task, "parent_task_id", "")
    if parent_task_id:
        pt = state.board_tasks.get(parent_task_id)
        if pt and pt.agent_id:
            pa = state.agents.get(pt.agent_id)
            if pa:
                parent_agent_id = pa.id
                parent_agent_name = pa.name
                parent_agent_slug = pa.slug or pa.name

    attachments = list(getattr(task, "attachments", []) or [])
    artifacts = list(getattr(task, "artifacts", []) or [])
    task_ctx = {
        "id": getattr(task, "id", ""),
        "title": getattr(task, "task", ""),
        "slug": getattr(task, "slug", ""),
        "description": getattr(task, "description", ""),
        "depth": getattr(task, "pipeline_depth", 0),
        "is_derived": bool(parent_task_id),
        "parent_task_id": parent_task_id,
        "parent_agent_id": parent_agent_id,
        "parent_agent_name": parent_agent_name,
        "parent_agent_slug": parent_agent_slug,
        "labels": list(getattr(task, "labels", []) or []),
        "group": getattr(task, "group", ""),
        "status": getattr(task, "status", ""),
        "verification_mode": getattr(task, "verification_mode", ""),
        "verification_state": getattr(task, "verification_state", ""),
        "verification_notes": getattr(task, "verification_notes", ""),
        "verification_updated_at": getattr(
            task, "verification_updated_at", ""),
        "verification_updated_by": getattr(
            task, "verification_updated_by", ""),
        "verification_summary": getattr(task, "verification_summary", {}) or {},
        "completion_evidence": getattr(task, "completion_evidence", {}) or {},
        "worktree_boundary": getattr(task, "worktree_boundary", {}) or {},
        "resume_after_boundary_task_id": getattr(
            task, "resume_after_boundary_task_id", "") or "",
        "attachments": [
            {"path": a.get("path", ""), "filename": a.get("filename", "")}
            for a in attachments
            if isinstance(a, dict)
        ],
        "artifacts": task_artifacts(attachments, artifacts),
        "upstream_artifacts": serialize_upstream_task_artifacts(
            task,
            tasks_by_id=state.board_tasks,
        ),
    }

    terminals_ctx = []
    for cid in state._children.get(cell_id, []):
        ch = state.agents.get(cid)
        if ch:
            terminals_ctx.append({
                "name": getattr(ch, "name", ""),
                "slug": getattr(ch, "slug", ""),
                "current_path": getattr(ch, "current_path", ""),
                "current_process": getattr(ch, "current_process", ""),
                "current_branch": getattr(ch, "current_branch", ""),
            })

    return {
        "agent": agent_ctx,
        "context": context_ctx,
        "worktree": worktree_ctx,
        "task": task_ctx,
        "terminals": terminals_ctx,
    }


def _resolve_memory_cell_and_task(state, cell_id: str = "",
                                  task_id: str = ""):
    """Resolve best-effort agent/task context for memory commands."""
    resolved_task = None
    resolved_cell = None

    task_id = str(task_id or "").strip()
    if task_id:
        resolved_task = state.board_tasks.get(state.resolve_task_alias(task_id))

    cell_id = str(cell_id or "").strip()
    if cell_id:
        resolved_cell = state.agents.get(cell_id)
        if resolved_task is None:
            resolved_task = detect_current_task(
                state,
                cell_id,
                explicit_task_id=task_id,
            )

    if resolved_cell is None and resolved_task and getattr(
        resolved_task, "agent_id", ""
    ):
        resolved_cell = state.agents.get(resolved_task.agent_id)

    return resolved_cell, resolved_task


def _resolve_memory_scope_ref(scope_kind: str, scope_ref: str = "",
                              *, cell=None, task=None) -> str:
    """Resolve memory scope refs from explicit values or active context."""
    kind = str(scope_kind or "").strip()
    ref = str(scope_ref or "").strip()
    if ref or not kind:
        return ref

    if kind == "task":
        if task and getattr(task, "id", ""):
            return task.id
        raise ValueError("Task scope requires an active task or scope_ref")

    if kind == "pipeline":
        if task:
            return getattr(task, "pipeline_root_id", "") or task.id
        raise ValueError("Pipeline scope requires an active task or scope_ref")

    if kind == "group":
        group_name = ""
        if task:
            group_name = getattr(task, "group", "") or ""
        if not group_name and cell:
            group_name = getattr(cell, "group", "") or ""
        if group_name:
            return group_name
        raise ValueError("Group scope requires a group or scope_ref")

    if kind == "project":
        project_key = infer_project_key(cell=cell, task=task)
        if project_key:
            return project_key
        raise ValueError(
            "Project scope requires a repo/worktree context or explicit scope_ref"
        )

    return ref
def _resolve_memory_link_ref(target_kind: str, target_ref: str = "",
                             *, cell=None, task=None) -> str:
    """Resolve memory-link target refs from explicit values or active context."""
    kind = str(target_kind or "").strip()
    ref = str(target_ref or "").strip()
    if ref:
        return ref

    if kind == "task":
        if task and getattr(task, "id", ""):
            return task.id
        raise ValueError("Task link requires an active task or target_ref")

    if kind == "agent":
        if cell and getattr(cell, "id", ""):
            return cell.id
        raise ValueError("Agent link requires an active agent or target_ref")

    if kind == "pipeline":
        if task:
            return getattr(task, "pipeline_root_id", "") or task.id
        raise ValueError("Pipeline link requires an active task or target_ref")

    return ref
