"""Human and Architect ask-task resolution commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ASK_COMMAND_NAMES = frozenset({"resolve_ask"})


@dataclass(slots=True)
class AskCommandRuntime:
    is_architect_ask_task: Any
    panel_event: Any
    resolve_architect_ask_task: Any
    resolve_human_ask_task: Any
    send_agent_prompt: Any
    bridge: Any
    state: Any


def _ask_command_response(data: dict, result: dict) -> dict:
    result["command"] = "resolve_ask"
    result["request_id"] = str(data.get("request_id", "") or "")
    return result


async def handle_ask_command(data: dict, runtime: AskCommandRuntime) -> dict:
    """Validate and resolve a tracked human-answer task."""
    task_id = data.get("id", "")
    answer = data.get("answer", "")
    task = runtime.state.board_tasks.get(task_id)
    if not task:
        return _ask_command_response(
            data, {"type": "error", "message": "Task not found"})
    if "torque:human" not in (task.labels or []):
        return _ask_command_response(
            data, {"type": "error", "message": "Not an ask task"})
    if not answer.strip():
        return _ask_command_response(
            data, {"type": "error", "message": "Answer is required"})
    if runtime.is_architect_ask_task(task):
        result = await runtime.resolve_architect_ask_task(
            runtime.state,
            runtime.bridge,
            task,
            answer,
            panel_event=runtime.panel_event,
        )
    else:
        result = await runtime.resolve_human_ask_task(
            runtime.state,
            task,
            answer,
            runtime.send_agent_prompt,
            panel_event=runtime.panel_event,
        )
    # UI callers use this correlation to retain their answer until the
    # authoritative resolve result arrives. MCP callers tolerate the additive
    # fields and continue to key success/error handling on ``type``.
    return _ask_command_response(data, result)
