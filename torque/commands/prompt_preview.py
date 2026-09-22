"""Worker prompt preview command assembly."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from ..actions import TORQUE_CONTEXT_STUB
from ..artifacts import normalize_artifacts, task_artifacts
from ..memory import build_prompt_memory_block
from ..server_agent import _append_task_artifacts
from ..server_artifacts import serialize_upstream_task_artifacts


PROMPT_PREVIEW_COMMAND_NAMES = frozenset({"preview_prompt"})


@dataclass(slots=True)
class PromptPreviewRuntime:
    assemble_worker_prompt: Any
    behavior_overlay_prompt_block_for_cell: Any
    build_postscript: Any
    build_torque_context: Any
    resolve_base_dir: Any
    action_mgr: Any
    state: Any
    template_mgr: Any


async def handle_prompt_preview_command(
    data: dict, runtime: PromptPreviewRuntime,
) -> dict:
    """Render the exact prompt body and postscript for a task or inline data."""
    _assemble_worker_prompt = runtime.assemble_worker_prompt
    _behavior_overlay_prompt_block_for_cell = (
        runtime.behavior_overlay_prompt_block_for_cell
    )
    _build_postscript = runtime.build_postscript
    _build_torque_context = runtime.build_torque_context
    _resolve_base_dir = runtime.resolve_base_dir
    action_mgr = runtime.action_mgr
    state = runtime.state
    template_mgr = runtime.template_mgr
    result = None

    # Preview rendered prompt for a task or inline params
    tid = data.get("id", "")
    preview_task = state.board_tasks.get(tid) if tid else None

    def draft_value(name, default=""):
        # Presence matters: an explicit empty value clears the saved field.
        return data[name] if name in data else getattr(preview_task, name, default)

    act_name = draft_value("action_name")
    preview_role_slug = str(draft_value("agent_template") or "").strip()
    task_text = draft_value("task")
    task_desc = draft_value("description")
    avars = draft_value("action_vars", {}) or {}
    act_group = draft_value("group")
    attachments = draft_value("attachments", []) or []
    artifacts = normalize_artifacts(draft_value("artifacts", []))
    preview_cell = None
    preview_agent_id = str(draft_value("agent_id") or "").strip()
    if preview_agent_id:
        preview_cell = state.agents.get(preview_agent_id)
    elif preview_role_slug:
        preview_cell = SimpleNamespace(
            id="",
            name="",
            slug="",
            group=act_group,
            cell_type="agent",
            agent_type="",
            directory="",
            kind="worker",
            role=preview_role_slug,
            template=preview_role_slug,
            owner_engineer_id="",
            created_by_engineer_id="",
            worktree_repo_root="",
            git_root="",
            worktree_branch="",
            worktree_auto_checkpoint=False,
            checkpoint_on_progress=False,
        )

    if preview_cell:
        preview_cell = copy(preview_cell)
        if "agent_template" in data:
            preview_cell.role = preview_role_slug
            preview_cell.template = preview_role_slug
        if "group" in data:
            preview_cell.group = act_group

    preview_task_obj = copy(preview_task) if preview_task else SimpleNamespace(
        id=tid,
        task=task_text,
        slug="",
        description=task_desc,
        pipeline_depth=0,
        parent_task_id=str(data.get("parent_task_id", "") or ""),
        pipeline_root_id="",
        labels=[],
        group=act_group,
        status="",
        verification_mode="",
        verification_state="",
        verification_notes="",
        verification_updated_at="",
        verification_updated_by="",
        verification_summary={},
        completion_evidence={},
        worktree_boundary={},
        resume_after_boundary_task_id="",
        attachments=attachments or [],
        artifacts=artifacts or [],
        action_name=act_name,
        agent_template=preview_role_slug,
        created_at="",
        updated_at="",
        agent_id=preview_agent_id,
    )
    # The copy retains identity, ancestry and verification metadata, while every
    # supplied draft field reaches TASK, torque context and the postscript alike.
    for name, value in {
        "task": task_text, "description": task_desc, "group": act_group,
        "action_name": act_name, "action_vars": avars,
        "agent_template": preview_role_slug, "agent_id": preview_agent_id,
        "attachments": attachments, "artifacts": artifacts,
    }.items():
        setattr(preview_task_obj, name, value)
    for name in (
        "labels", "parent_task_id", "verification_mode", "verification_state",
        "verification_notes", "verification_summary",
    ):
        if name in data:
            setattr(preview_task_obj, name, data[name])
    preview_upstream_artifacts = serialize_upstream_task_artifacts(
        preview_task_obj,
        tasks_by_id=state.board_tasks,
    )

    if preview_cell:
        torque_ctx = _build_torque_context(
            state, preview_cell, preview_task_obj)
        is_clean = torque_ctx["context"]["is_clean"]
        shared_context_block = build_prompt_memory_block(
            state.db,
            cell=preview_cell,
            task=preview_task_obj,
        )
    else:
        torque_ctx = {
            **TORQUE_CONTEXT_STUB,
            "task": {
                **TORQUE_CONTEXT_STUB["task"],
                "id": tid,
                "slug": getattr(preview_task_obj, "slug", ""),
                "depth": getattr(preview_task_obj, "pipeline_depth", 0),
                "is_derived": bool(preview_task_obj.parent_task_id),
                "parent_task_id": preview_task_obj.parent_task_id,
                "labels": preview_task_obj.labels,
                "status": preview_task_obj.status,
                "verification_mode": preview_task_obj.verification_mode,
                "verification_state": preview_task_obj.verification_state,
                "verification_notes": preview_task_obj.verification_notes,
                "verification_summary": preview_task_obj.verification_summary,
                "title": task_text,
                "description": task_desc,
                "group": act_group,
                "attachments": [
                    {"path": a.get("path", ""),
                     "filename": a.get("filename", "")}
                    for a in (attachments or [])
                    if isinstance(a, dict)
                ],
                "artifacts": task_artifacts(
                    attachments or [],
                    artifacts or [],
                ),
                "upstream_artifacts": preview_upstream_artifacts,
            },
        }
        is_clean = True
        shared_context_block = ""

    base_dir = (
        preview_cell.worktree_repo_root
        or preview_cell.directory
    ) if preview_cell else ""
    if not base_dir:
        base_dir = await _resolve_base_dir(act_group)

    prompt_text = task_text
    disable_role_preamble = False
    if act_name:
        rendered_action = action_mgr.render_action(
            act_name,
            {"TASK": task_text, **avars},
            base_dir=base_dir,
            torque_context=torque_ctx)
        if not rendered_action:
            result = {"type": "prompt_preview",
                      "prompt": task_text,
                      "warning": f"Action "
                                 f"\"{act_name}\" not found"}
        else:
            prompt_text = rendered_action.get("prompt", "")
            disable_role_preamble = bool(
                rendered_action.get(
                    "disable_role_preamble", False)
            )

    if result is None:
        prompt_text = _append_task_artifacts(
            prompt_text,
            attachments,
            artifacts,
            preview_upstream_artifacts,
        )
        prompt_text += shared_context_block
        postscript = _build_postscript(
            preview_task_obj,
            action_mgr,
            base_dir if act_name else "",
            is_clean=is_clean,
            cell=preview_cell,
        )
        result = {
            "type": "prompt_preview",
            "prompt": _assemble_worker_prompt(
                role_mgr=template_mgr,
                cell=preview_cell,
                base_dir=base_dir,
                prompt_body=prompt_text,
                postscript=postscript,
                behavior_overlay_block=
                _behavior_overlay_prompt_block_for_cell(
                    state,
                    cell=preview_cell,
                    include_agent=False,
                    worker_dispatch=True,
                ),
                disable_role_preamble=disable_role_preamble,
            ),
        }

    if tid:
        # Echo the target so concurrent React inspectors can retain previews
        # without a global last-response race. Legacy consumers ignore it.
        result["id"] = tid
        result["task_id"] = tid
    return result
