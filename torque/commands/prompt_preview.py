"""Worker prompt preview command assembly."""

from __future__ import annotations

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
    act_name = data.get("action_name", "")
    preview_role_slug = str(
        data.get("agent_template", "") or ""
    ).strip()
    task_text = data.get("task", "")
    avars = data.get("action_vars", {})
    act_group = data.get("group", "")
    attachments = data.get("attachments", [])
    artifacts = normalize_artifacts(data.get("artifacts", []))

    if tid and not task_text:
        t = state.board_tasks.get(tid)
        if t:
            task_text = t.task
            act_name = act_name or t.action_name
            preview_role_slug = (
                preview_role_slug
                or str(t.agent_template or "").strip()
            )
            avars = avars or t.action_vars or {}
            act_group = act_group or t.group
            attachments = t.attachments or []
            artifacts = normalize_artifacts(t.artifacts or [])

    task_desc = data.get("description", "")
    if tid and not task_desc:
        t = state.board_tasks.get(tid)
        if t:
            task_desc = t.description or ""

    preview_task = state.board_tasks.get(tid) if tid else None
    preview_cell = None
    preview_agent_id = str(data.get("agent_id", "") or "").strip()
    if preview_agent_id:
        preview_cell = state.agents.get(preview_agent_id)
    elif preview_task and preview_task.agent_id:
        preview_cell = state.agents.get(preview_task.agent_id)
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

    preview_task_obj = preview_task or SimpleNamespace(
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

    return result
