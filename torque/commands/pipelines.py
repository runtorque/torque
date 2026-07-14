"""Pipeline discovery and task-chain query commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PIPELINE_COMMAND_NAMES = frozenset({"task_chain", "discover_pipelines"})


@dataclass(slots=True)
class PipelineCommandRuntime:
    resolve_base_dir: Any
    action_mgr: Any
    state: Any


async def handle_pipeline_command(
    data: dict, runtime: PipelineCommandRuntime,
) -> dict:
    """Return a task chain or the pipelines available to a group."""
    cmd = str(data.get("cmd", "") or "").strip()
    if cmd == "task_chain":
        task_id = data.get("task_id", "")
        task = runtime.state.board_tasks.get(task_id)
        if not task:
            return {"type": "error", "message": "Task not found"}
        chain = runtime.state.board_get_chain(task_id)
        return {
            "type": "ok",
            "root_id": task.pipeline_root_id or task.id,
            "chain": [
                {
                    "id": item.id,
                    "task": item.task,
                    "lane": item.lane,
                    "status": item.status,
                    "depth": item.pipeline_depth,
                    "agent_id": item.agent_id,
                    "action_name": item.action_name,
                    "parent_task_id": item.parent_task_id,
                    "labels": item.labels,
                }
                for item in chain
            ],
        }

    base_dir = await runtime.resolve_base_dir(data.get("group", ""))
    return {
        "type": "pipelines",
        "pipelines": runtime.action_mgr.discover_pipelines(base_dir),
    }
