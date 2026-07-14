"""Shared context contract for domain-scoped MCP dispatchers."""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


UNHANDLED = object()


@dataclass(slots=True)
class ScopedDispatchContext:
    """Authenticated state and request data passed to one domain dispatcher."""

    name: str
    args: dict
    handle_command: Callable[[dict], Awaitable[dict]]
    state: Any
    real_state: Any
    tool_prefix: str
    caller_kind: str
    caller_id: str
    idempotency_key: str
    caller_cell: Any
    caller_group: str
