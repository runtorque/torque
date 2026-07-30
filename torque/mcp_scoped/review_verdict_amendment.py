"""Worker report schema and dispatch for append-only review amendments."""

from __future__ import annotations

import json
import time

from torque.server_review import _amend_review_verdict_evidence


REVIEW_VERDICT_AMEND_TOOL = {
    "name": "torque_review_verdict_amend",
    "authority": {"requirements": [{
        "capability": "task.review.amend",
        "minimum_scope": "self",
        "target_argument": "task",
        "target_kind": "task",
    }]},
    "description": (
        "Append an attributable correction to this reviewer's own recorded "
        "structured unknown final verdict. The original verdict remains "
        "visible; use after completion or merge with the explicit review task id."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The completed feature/review task ID or alias.",
            },
            "verdict": {
                "type": "string",
                "enum": ["ship", "block", "needs_followup"],
                "description": "The explicit corrected verdict.",
            },
            "reason": {
                "type": "string",
                "description": "Why the recorded unknown verdict is wrong.",
            },
        },
        "required": ["task", "verdict", "reason"],
    },
}


def dispatch_review_verdict_amendment(name, args, cell_id, state):
    """Handle the exact review-amendment route or return ``None``."""
    if name != "torque_review_verdict_amend":
        return None
    cell = state.agents.get(str(cell_id or "").strip()) if cell_id else None
    if not cell:
        return f"Agent {cell_id} not found", True
    if state.agent_is_tombstoned(cell):
        return f"Agent {cell_id} is tombstoned", True
    task_id = state.resolve_board_task_id(str(args.get("task", "") or ""))
    task = state.board_tasks.get(task_id) if task_id else None
    if not task:
        return "Task not found", True

    def append_task_message(current_task, action, message, agent_name):
        current_task.messages.append({
            "timestamp": time.time(),
            "action": action,
            "message": message,
            "agent_name": agent_name,
        })

    amendment, error = _amend_review_verdict_evidence(
        state,
        task,
        cell=cell,
        verdict=args.get("verdict", ""),
        reason=args.get("reason", ""),
        append_task_msg=append_task_message,
    )
    if error:
        return error, True
    return json.dumps({
        "type": "review_verdict_amended",
        "task_id": task.id,
        "amendment": amendment,
        "original_review": task.completion_evidence.get("review", {}),
    }), False
