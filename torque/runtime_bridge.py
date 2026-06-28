"""Terminal-compatible agent runtime bridge.

Routes legacy PTY cells unchanged and explicit codex-sdk-readonly cells to the
read-only SDK runner while preserving the TerminalAdapter surface.
"""

from __future__ import annotations

import inspect
from typing import Any

from .codex_sdk_runner import CodexSdkReadonlyRunner
from .runner_backends import CODEX_SDK_READONLY_BACKEND, is_codex_sdk_readonly


class AgentRuntimeBridge:
    """Facade over PTY bridge plus the optional Codex SDK read-only runner."""

    def __init__(self, state, pty_bridge, *, sdk_runner: CodexSdkReadonlyRunner | None = None):
        self.state = state
        self.pty = pty_bridge
        self.sdk = sdk_runner or CodexSdkReadonlyRunner(state)
        self.capabilities = getattr(pty_bridge, "capabilities", None)
        self.on_session_terminated = None
        self.on_agent_session_end_detected = None
        self.on_terminal_disconnected = None
        self.on_terminal_output = None
        self.on_agent_event = None

    def _wire_callbacks(self) -> None:
        self.pty.on_session_terminated = self.on_session_terminated
        self.pty.on_agent_session_end_detected = self.on_agent_session_end_detected
        self.pty.on_terminal_disconnected = self.on_terminal_disconnected
        self.pty.on_terminal_output = self.on_terminal_output
        self.sdk.event_sink = self.on_agent_event
        self.sdk.terminal_output = self.on_terminal_output

    def _cell_for_session(self, session_id: str):
        for cell in getattr(self.state, "agents", {}).values():
            if getattr(cell, "session_id", None) == session_id:
                return cell
        return None

    async def start(self) -> None:
        self._wire_callbacks()
        result = self.pty.start()
        if inspect.isawaitable(result):
            await result

    async def reconnect_orphans(self) -> None:
        self._wire_callbacks()
        await self.sdk.reconnect_orphans()
        result = self.pty.reconnect_orphans()
        if inspect.isawaitable(result):
            await result

    async def shutdown(self) -> None:
        for session_id in list(self.sdk.sessions):
            await self.sdk.close_session(session_id)
        result = self.pty.shutdown()
        if inspect.isawaitable(result):
            await result

    async def create_session(self, cell, **kwargs) -> None:
        self._wire_callbacks()
        if is_codex_sdk_readonly(cell):
            return await self.sdk.create_session(cell, **kwargs)
        return await self.pty.create_session(cell, **kwargs)

    async def close_session(self, session_id: str) -> None:
        cell = self._cell_for_session(session_id)
        if is_codex_sdk_readonly(cell) or str(session_id or "").startswith("sdk-"):
            return await self.sdk.close_session(session_id)
        return await self.pty.close_session(session_id)

    async def focus_session(self, session_id: str, *, client_id: str = "") -> bool:
        cell = self._cell_for_session(session_id)
        if is_codex_sdk_readonly(cell) or str(session_id or "").startswith("sdk-"):
            return False
        return await self.pty.focus_session(session_id, client_id=client_id)

    async def update_session(self, cell, old_name: str = "") -> None:
        if is_codex_sdk_readonly(cell):
            self.state._emit_agent(cell)
            self.state._db_save_agent(cell)
            await self.state.broadcast()
            return
        return await self.pty.update_session(cell, old_name=old_name)

    async def send_text(self, session_id: str, text: str, **kwargs) -> None:
        cell = self._cell_for_session(session_id)
        if is_codex_sdk_readonly(cell) or str(session_id or "").startswith("sdk-"):
            return await self.sdk.send_text(session_id, text)
        return await self.pty.send_text(session_id, text, **kwargs)

    async def write_input(self, session_id: str, data: str) -> None:
        cell = self._cell_for_session(session_id)
        if is_codex_sdk_readonly(cell) or str(session_id or "").startswith("sdk-"):
            return
        return await self.pty.write_input(session_id, data)

    async def reorder_tabs(self) -> None:
        return await self.pty.reorder_tabs()

    async def list_profiles(self) -> list[str]:
        return await self.pty.list_profiles()

    async def get_launch_context(self):
        return await self.pty.get_launch_context()

    def prime_input_ready(self, session_id: str) -> None:
        cell = self._cell_for_session(session_id)
        if is_codex_sdk_readonly(cell) or str(session_id or "").startswith("sdk-"):
            return
        return self.pty.prime_input_ready(session_id)

    def signal_input_ready(self, cell_id: str) -> None:
        cell = getattr(self.state, "agents", {}).get(cell_id)
        if is_codex_sdk_readonly(cell):
            return
        return self.pty.signal_input_ready(cell_id)

    async def register_web_view_tool(self, **kwargs) -> bool:
        return await self.pty.register_web_view_tool(**kwargs)

    async def resize_session(self, session_id: str, cols: int, rows: int) -> None:
        cell = self._cell_for_session(session_id)
        if is_codex_sdk_readonly(cell) or str(session_id or "").startswith("sdk-"):
            return
        return await self.pty.resize_session(session_id, cols, rows)

    def get_terminal_buffer(self, session_id: str) -> str:
        cell = self._cell_for_session(session_id)
        if is_codex_sdk_readonly(cell) or str(session_id or "").startswith("sdk-"):
            return self.sdk.get_terminal_buffer(session_id)
        return self.pty.get_terminal_buffer(session_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.pty, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {
            "on_supervisor_event",
        } and "pty" in self.__dict__ and hasattr(self.__dict__["pty"], name):
            setattr(self.__dict__["pty"], name, value)
            return
        object.__setattr__(self, name, value)
