"""Terminal adapter protocol — abstracts the terminal emulator backend.

This protocol defines the interface that bridge implementations must satisfy.
The iTerm2 adapter (bridge.py) is the current implementation; future adapters
(e.g. Ghostty) can implement the same protocol to support different terminals.

Note: this is a *terminal emulator* adapter, distinct from the *agent type*
adapters in loom/adapters/ which handle AI agent awareness (Claude Code, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .state import AgentCell


@runtime_checkable
class TerminalAdapter(Protocol):
    """Interface for controlling a terminal emulator."""

    on_session_terminated: Optional[Callable]
    on_terminal_disconnected: Optional[Callable]

    async def start(self) -> None:
        """Start background monitors (focus, termination, etc.)."""
        ...

    async def reconnect_orphans(self) -> None:
        """Re-link persisted cells to existing terminal sessions after restart."""
        ...

    async def create_session(
        self,
        cell: AgentCell,
        *,
        env_vars: dict[str, str] | None = None,
        init_script: str = "",
        shell: str = "",
        target_session_id: str = "",
        target_window_id: str = "",
    ) -> None:
        """Create a new terminal session for the given cell."""
        ...

    async def close_session(self, session_id: str) -> None:
        """Close a terminal session."""
        ...

    async def focus_session(self, session_id: str) -> bool:
        """Focus/activate a terminal session. Returns True if found."""
        ...

    async def update_session(self, cell: AgentCell, old_name: str = "") -> None:
        """Apply name and tab color changes to a live session."""
        ...

    async def send_text(self, session_id: str, text: str) -> None:
        """Send text/keystrokes to a terminal session."""
        ...

    async def reorder_tabs(self) -> None:
        """Reorder terminal tabs to match the Loom grid order."""
        ...
