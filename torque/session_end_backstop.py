"""Hook-independent session-end backstop helpers for interactive agents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _IdleBackstopState:
    session_id: str = ""
    saw_running: bool = False
    saw_busy: bool = False
    stable_ready_polls: int = 0
    emitted: bool = False


class CodexIdleSessionEndDetector:
    """Detect a Codex turn finishing from a busy -> idle-composer transition.

    Codex CLI releases have drifted away from the hook payloads Torque expects,
    so the Stop hook can disappear while the interactive TUI remains alive at
    the composer.  This detector is intentionally state-only: callers provide
    the current cell plus whether the current PTY screen matches Codex's input
    ready screen, and it returns True exactly once per observed running turn
    after a stable busy -> ready transition.
    """

    def __init__(self) -> None:
        self._states: dict[str, _IdleBackstopState] = {}

    def reset(self, cell_id: str, session_id: str = "") -> None:
        """Forget any prior turn state for ``cell_id``.

        ``session_id`` seeds the new state so a follow-up prompt sent before
        the monitor observes the previous idle state still starts a fresh turn.
        """
        cell_id = str(cell_id or "")
        if not cell_id:
            return
        self._states[cell_id] = _IdleBackstopState(session_id=str(session_id or ""))

    def discard(self, cell_id: str) -> None:
        self._states.pop(str(cell_id or ""), None)

    def clear(self) -> None:
        self._states.clear()

    def observe(self, cell, *, ready: bool, stable_polls: int) -> bool:
        """Return True when this observation completes a Codex turn.

        The detector is scoped to live Codex agent cells in ``running`` status.
        It requires that the running turn first show a non-ready screen and then
        a stable ready screen.  That prevents the pre-submit composer (still in
        scrollback or visible while Torque is writing the prompt) from being
        mistaken for turn completion, and prevents repeat events while idle.
        """
        cell_id = str(getattr(cell, "id", "") or "")
        if not cell_id:
            return False

        session_id = str(getattr(cell, "session_id", "") or "")
        is_codex_agent = (
            str(getattr(cell, "cell_type", "") or "") == "agent"
            and str(getattr(cell, "agent_type", "") or "") == "codex"
            and bool(session_id)
        )
        if not is_codex_agent:
            self.discard(cell_id)
            return False

        state = self._states.get(cell_id)
        if state is None or state.session_id != session_id:
            state = _IdleBackstopState(session_id=session_id)
            self._states[cell_id] = state

        if str(getattr(cell, "status", "") or "") != "running":
            # Idle/stopped at rest is not a transition.  Reset so the next
            # prompt must observe busy before it can finish.
            state.saw_running = False
            state.saw_busy = False
            state.stable_ready_polls = 0
            state.emitted = False
            return False

        if not state.saw_running:
            state.saw_running = True
            state.saw_busy = False
            state.stable_ready_polls = 0
            state.emitted = False

        if not ready:
            state.saw_busy = True
            state.stable_ready_polls = 0
            return False

        if not state.saw_busy:
            # Do not count the old composer as completion until we have seen
            # the screen leave input-ready during this running turn.
            state.stable_ready_polls = 0
            return False

        if state.emitted:
            return False

        state.stable_ready_polls += 1
        if state.stable_ready_polls >= max(int(stable_polls or 1), 1):
            state.emitted = True
            return True
        return False
