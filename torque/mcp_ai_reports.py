"""Transport truth helpers for worker AI-report MCP actions."""

from __future__ import annotations


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
