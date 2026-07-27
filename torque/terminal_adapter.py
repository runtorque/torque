"""Terminal adapter protocol — abstracts the terminal emulator backend.

This protocol defines the interface that bridge implementations must satisfy.
The supervised PTY adapter is the current implementation; future adapters can
implement the same protocol to support different terminal runtimes.

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


class TerminalInputDeliveryError(RuntimeError):
    """A terminal may have started an input delivery but could not finish it.

    ``write_input`` deliberately distinguishes this from a session that is
    already gone: callers receive ``False`` for the latter intentional no-op.
    This exception means the write may have delivered a prefix, so callers
    must not submit a successor on that same session.
    """


class TerminalInputUnavailableError(TerminalInputDeliveryError):
    """A verified preflight failure where no input delivery was attempted.

    This remains a visible prompt failure, rather than a success-shaped drop,
    but queueing callers may safely continue with a later retry because no
    prefix could have reached the terminal.
    """


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
        sdk_system_prompt: str = "",
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

    async def focus_session(self, session_id: str, *, client_id: str = "") -> bool:
        """Focus/activate a terminal session. Returns True if found."""
        ...

    async def update_session(self, cell: AgentCell, old_name: str = "") -> None:
        """Apply name and tab color changes to a live session."""
        ...

    async def send_text(self, session_id: str, text: str) -> bool:
        """Send text/keystrokes; return whether a live session received it.

        Raises :class:`TerminalInputDeliveryError` when delivery may have
        started but cannot finish, or :class:`TerminalInputUnavailableError`
        when a preflight availability check proves that no input was sent.
        """
        ...

    async def interrupt_active_turn(self, session_id: str) -> bool:
        """Interrupt the active provider turn when the adapter supports it."""
        ...

    async def write_input(self, session_id: str, data: str) -> bool:
        """Write all raw terminal input bytes, returning ``False`` only when
        the session is absent/closed.

        Empty input is a successful no-op.  An attempted write that cannot
        deliver every byte must raise :class:`TerminalInputDeliveryError`;
        a preflight failure that attempted no write raises
        :class:`TerminalInputUnavailableError`.
        """
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
