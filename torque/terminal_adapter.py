"""Terminal adapter protocol — abstracts the terminal emulator backend.

This protocol defines the interface that bridge implementations must satisfy.
The iTerm2 adapter (bridge.py) is the current implementation; future adapters
(e.g. Ghostty) can implement the same protocol to support different terminals.

Note: this is a *terminal emulator* adapter, distinct from the *agent type*
adapters in torque/adapters/ which handle AI agent awareness (Claude Code, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .state import AgentCell


@dataclass(frozen=True)
class TerminalCapabilities:
    """Feature flags for a terminal backend."""

    supports_profiles: bool = False
    supports_embedded_terminal: bool = False
    supports_tab_color: bool = False
    supports_tab_reorder: bool = False
    supports_focus_tracking: bool = False
    supports_global_keybindings: bool = False
    supports_toolbelt_registration: bool = False


@dataclass(frozen=True)
class TerminalLaunchContext:
    """Best-effort launch context for creating new sessions."""

    current_path: str = ""
    current_profile: str = "Default"
    current_window_id: str = ""
    active_session_id: str = ""


@runtime_checkable
class TerminalAdapter(Protocol):
    """Interface for controlling a terminal emulator."""

    capabilities: TerminalCapabilities
    on_session_terminated: Optional[Callable]
    on_agent_session_end_detected: Optional[Callable]
    on_terminal_disconnected: Optional[Callable]
    on_terminal_output: Optional[Callable]

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
        env_file: str = "",
        init_script: str = "",
        shell: str = "",
        system_prompt: str = "",
        mcp_entrypoint: str = "",
        target_session_id: str = "",
        target_window_id: str = "",
        restore_focus_to_prev_tab: bool = False,
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

    async def write_input(self, session_id: str, data: str) -> None:
        """Write raw terminal input bytes without submit handling."""
        ...

    async def reorder_tabs(self) -> None:
        """Reorder terminal tabs to match the Torque grid order."""
        ...

    async def list_profiles(self) -> list[str]:
        """Return backend-specific launch profiles, if any."""
        ...

    async def get_launch_context(self) -> TerminalLaunchContext:
        """Return the active launch context for creating new sessions."""
        ...

    def prime_input_ready(self, session_id: str) -> None:
        """Skip the first input-ready wait for the given session."""
        ...

    def signal_input_ready(self, cell_id: str) -> None:
        """Mark an agent UI as ready to accept Torque prompts."""
        ...

    async def register_web_view_tool(
        self,
        *,
        display_name: str,
        identifier: str,
        url: str,
        reveal_if_already_registered: bool = True,
    ) -> bool:
        """Register a native webview/toolbelt surface if the backend supports it."""
        ...

    async def resize_session(self, session_id: str, cols: int, rows: int) -> None:
        """Resize a live terminal session."""
        ...

    def get_terminal_buffer(self, session_id: str) -> str:
        """Return the recent output buffer for an embedded terminal session."""
        ...

    async def shutdown(self) -> None:
        """Release backend resources before the Torque daemon exits."""
        ...
