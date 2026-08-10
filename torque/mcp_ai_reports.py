"""Transport truth helpers for worker AI-report MCP actions."""

from __future__ import annotations


DERIVE_TOOL_SPEC = {
    "name": "torque_derive",
    "authority": {
        "requirements": [{
            "capability": "task.report",
            "minimum_scope": "self",
            "handler_scoped": True,
        }],
    },
    "description": (
        "Derive a subtask and dispatch it. The parent task stays "
        "In Progress with a status badge while the derived task "
        "is worked on. The action's transitions field controls "
        "which actions can be derived and where the task is routed "
        "(new agent, self, parent agent, or root agent). For "
        "feature/review, prior-reviewer reuse is disclosed in the "
        "result and review record. Set require_fresh_reviewer to make "
        "same-task prior-reviewer exclusion a routing constraint; "
        "context prose does not impose reviewer exclusions."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short title for the derived task.",
            },
            "context": {
                "type": "string",
                "description": "Longer description or context.",
            },
            "action": {
                "type": "string",
                "description": (
                    "Action name for the derived task "
                    "(e.g. 'pipeline/review')."
                ),
            },
            "action_vars": {
                "type": "object",
                "description": "Action variables as key-value pairs.",
                "additionalProperties": {"type": "string"},
            },
            "group": {
                "type": "string",
                "description": (
                    "Target group (defaults to current task's group)."
                ),
            },
            "require_fresh_reviewer": {
                "type": "boolean",
                "description": (
                    "For feature/review only, require automatic routing "
                    "to create a seat not previously assigned to review "
                    "this task chain. Default false preserves block/fix "
                    "re-review continuity."
                ),
            },
        },
        "required": ["description"],
    },
}


def ai_report_transport_override(result, action):
    """Return a truthful MCP tuple for retryable or missing report results."""
    if result and result.get("type") == "error" and result.get("retryable"):
        return result.get("message", "Retryable report refusal."), True, False
    if not result:
        return (
            f"AI report action {action} returned no result; refusing to "
            "report success. Retry after inspecting task context.",
            True,
            False,
        )
    return None
