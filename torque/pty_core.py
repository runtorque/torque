"""Shared PTY helpers used by the in-daemon adapter and the supervisor.

This module contains only platform-level building blocks (constants,
regex patterns, ctty acquisition, winsize ioctl) that are independent of
Torque's agent/state model. It intentionally has no imports from the rest
of the ``torque`` package so the supervisor can use it in isolation.
"""

from __future__ import annotations

import codecs
import contextlib
import fcntl
import re
import struct
import termios

# Maximum number of output bytes buffered per session. The ring buffer is
# replayed to a subscriber on (re)connect.
BUFFER_LIMIT = 200_000

# Tail window used when scanning for shell-integration (OSC7) sequences.
PROMPT_HOOK_LIMIT = 512

# Tail window used when scanning the screen for agent input-readiness.
READINESS_BUFFER_LIMIT = 20_000

# Maximum pending (not yet replayed) output characters retained by
# TerminalScreenBuffer between renders. The screen itself is only a few KB;
# this window holds many full TUI repaints, so replaying just the tail after
# an overflow still reconstructs the current screen.
SCREEN_PENDING_LIMIT = 48_000

# Matches OSC7 cwd reports emitted by our zsh/bash prompt hooks.
OSC7_RE = re.compile("\x1b]7;file://[^/\x07\x1b]*(/.*?)(?:\x07|\x1b\\\\)")

# Matches ANSI escape sequences so we can strip them out of screen text.
ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1B\\))",
    re.DOTALL,
)

Utf8IncrementalDecoder = codecs.getincrementaldecoder("utf-8")


class TerminalScreenBuffer:
    """Small current-screen model for PTY output.

    Torque keeps an append-only terminal buffer for replay in the browser, but
    readiness checks need the *current* TUI screen. This is intentionally not a
    full terminal emulator; it covers the ANSI cursor movement and clearing
    sequences used by interactive agent CLIs when repainting their composer.
    """

    def __init__(self, cols: int = 120, rows: int = 32):
        self.cols = max(1, int(cols or 1))
        self.rows = max(1, int(rows or 1))
        self._lines: list[list[str]] = [[] for _ in range(self.rows)]
        self._row = 0
        self._col = 0
        self._saved_cursor = (0, 0)
        self._pending: list[str] = []
        self._pending_chars = 0
        self._pending_overflow = False

    def reset(self) -> None:
        self._lines = [[] for _ in range(self.rows)]
        self._row = 0
        self._col = 0
        self._pending.clear()
        self._pending_chars = 0
        self._pending_overflow = False

    def resize(self, cols: int, rows: int) -> None:
        # Replay under the geometry the pending output was produced for.
        self._flush_pending()
        self.cols = max(1, int(cols or 1))
        new_rows = max(1, int(rows or 1))
        if new_rows > len(self._lines):
            self._lines.extend([] for _ in range(new_rows - len(self._lines)))
        elif new_rows < len(self._lines):
            self._lines = self._lines[:new_rows]
        self.rows = new_rows
        self._lines = [line[:self.cols] for line in self._lines]
        self._row = min(self._row, self.rows - 1)
        self._col = min(self._col, self.cols - 1)
        saved_row, saved_col = self._saved_cursor
        self._saved_cursor = (
            min(saved_row, self.rows - 1),
            min(saved_col, self.cols - 1),
        )

    def feed(self, text: str) -> None:
        """Queue output for lazy replay.

        Feeding used to emulate the terminal per character on every PTY
        chunk — hundreds of milliseconds of event-loop CPU per MB of agent
        output. The current screen is only consulted at readiness-poll
        cadence, so emulation is deferred to ``render()``. Beyond
        ``SCREEN_PENDING_LIMIT`` the oldest pending output is dropped and
        the screen rebuilt from the retained tail, which spans many full
        TUI repaints.
        """
        if not text:
            return
        self._pending.append(text)
        self._pending_chars += len(text)
        while (self._pending_chars > SCREEN_PENDING_LIMIT
                and len(self._pending) > 1):
            dropped = self._pending.pop(0)
            self._pending_chars -= len(dropped)
            self._pending_overflow = True
        if self._pending_chars > SCREEN_PENDING_LIMIT:
            tail = self._pending[0][-SCREEN_PENDING_LIMIT:]
            self._pending[0] = tail
            self._pending_chars = len(tail)
            self._pending_overflow = True

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        chunks = self._pending
        self._pending = []
        self._pending_chars = 0
        if self._pending_overflow:
            self._pending_overflow = False
            # Continuity with the old screen is broken; rebuild from the
            # retained tail, which repaints it within a frame or two.
            self._lines = [[] for _ in range(self.rows)]
            self._row = 0
            self._col = 0
        for chunk in chunks:
            self._feed_now(chunk)

    def _feed_now(self, text: str) -> None:
        i = 0
        length = len(text)
        while i < length:
            ch = text[i]
            if ch == "\x1b":
                i = self._consume_escape(text, i)
                continue
            if ch == "\r":
                self._col = 0
                i += 1
                continue
            if ch == "\n":
                self._linefeed()
                i += 1
                continue
            if ch == "\b":
                self._col = max(0, self._col - 1)
                i += 1
                continue
            if ch == "\t":
                spaces = 8 - (self._col % 8)
                for _ in range(spaces):
                    self._put_char(" ")
                i += 1
                continue
            codepoint = ord(ch)
            if codepoint < 32 or 0x7F <= codepoint < 0xA0:
                i += 1
                continue
            self._put_char(ch)
            i += 1

    def render(self) -> str:
        self._flush_pending()
        lines = ["".join(line).rstrip() for line in self._lines]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def _consume_escape(self, text: str, start: int) -> int:
        if start + 1 >= len(text):
            return len(text)
        introducer = text[start + 1]
        if introducer == "[":
            end = start + 2
            while end < len(text):
                if 0x40 <= ord(text[end]) <= 0x7E:
                    params = text[start + 2:end]
                    self._handle_csi(params, text[end])
                    return end + 1
                end += 1
            return len(text)
        if introducer == "]":
            return self._consume_osc(text, start + 2)
        if introducer == "7":
            self._saved_cursor = (self._row, self._col)
            return start + 2
        if introducer == "8":
            self._restore_cursor()
            return start + 2
        return start + 2

    def _consume_osc(self, text: str, start: int) -> int:
        i = start
        while i < len(text):
            if text[i] == "\x07":
                return i + 1
            if text[i] == "\x1b" and i + 1 < len(text) and text[i + 1] == "\\":
                return i + 2
            i += 1
        return len(text)

    def _handle_csi(self, params: str, final: str) -> None:
        private = params.startswith("?")
        if final in ("H", "f"):
            row, col = self._cursor_position_params(params)
            self._move_to(row - 1, col - 1)
            return
        if final == "A":
            self._row = max(0, self._row - self._first_param(params, 1))
            return
        if final == "B":
            self._row = min(
                self.rows - 1, self._row + self._first_param(params, 1))
            return
        if final == "C":
            self._col = min(
                self.cols - 1, self._col + self._first_param(params, 1))
            return
        if final == "D":
            self._col = max(0, self._col - self._first_param(params, 1))
            return
        if final == "E":
            self._row = min(
                self.rows - 1, self._row + self._first_param(params, 1))
            self._col = 0
            return
        if final == "F":
            self._row = max(0, self._row - self._first_param(params, 1))
            self._col = 0
            return
        if final == "G":
            self._col = min(self.cols - 1, self._first_param(params, 1) - 1)
            return
        if final == "d":
            self._row = min(self.rows - 1, self._first_param(params, 1) - 1)
            return
        if final == "J":
            self._erase_display(self._first_param(params, 0))
            return
        if final == "K":
            self._erase_line(self._first_param(params, 0))
            return
        if final == "s":
            self._saved_cursor = (self._row, self._col)
            return
        if final == "u":
            self._restore_cursor()
            return
        if final in ("h", "l") and private:
            modes = self._all_params(params)
            if any(mode in (47, 1047, 1049) for mode in modes):
                self.reset()

    def _linefeed(self) -> None:
        self._row += 1
        if self._row < self.rows:
            return
        del self._lines[0]
        self._lines.append([])
        self._row = self.rows - 1

    def _put_char(self, ch: str) -> None:
        if self._col >= self.cols:
            self._col = 0
            self._linefeed()
        line = self._lines[self._row]
        if self._col >= len(line):
            line.extend(" " for _ in range(self._col - len(line)))
            line.append(ch)
        else:
            line[self._col] = ch
        self._col += 1
        if self._col >= self.cols:
            self._col = 0
            self._linefeed()

    def _erase_display(self, mode: int) -> None:
        if mode in (2, 3):
            self.reset()
            return
        if mode == 1:
            for row in range(0, self._row):
                self._lines[row] = []
            line = self._lines[self._row]
            if line:
                end = min(self._col + 1, len(line))
                line[:end] = [" "] * end
            return
        self._erase_line(0)
        for row in range(self._row + 1, self.rows):
            self._lines[row] = []

    def _erase_line(self, mode: int) -> None:
        line = self._lines[self._row]
        if mode == 2:
            self._lines[self._row] = []
            return
        if mode == 1:
            if not line:
                return
            end = min(self._col + 1, len(line))
            line[:end] = [" "] * end
            return
        if self._col < len(line):
            del line[self._col:]

    def _move_to(self, row: int, col: int) -> None:
        self._row = min(max(0, row), self.rows - 1)
        self._col = min(max(0, col), self.cols - 1)

    def _restore_cursor(self) -> None:
        row, col = self._saved_cursor
        self._move_to(row, col)

    def _first_param(self, params: str, default: int) -> int:
        values = self._all_params(params)
        return values[0] if values else default

    def _all_params(self, params: str) -> list[int]:
        values: list[int] = []
        for match in re.finditer(r"\d+", params):
            with contextlib.suppress(ValueError):
                values.append(int(match.group(0)))
        return values

    def _cursor_position_params(self, params: str) -> tuple[int, int]:
        raw = params.lstrip("?")
        parts = raw.split(";") if raw else []

        def _part(index: int) -> int:
            if index >= len(parts) or not parts[index]:
                return 1
            with contextlib.suppress(ValueError):
                return max(1, int(parts[index]))
            return 1

        return (_part(0), _part(1))


def preexec_acquire_ctty() -> None:
    """Make the PTY slave (stdin after dup2) the controlling terminal.

    subprocess with ``start_new_session=True`` calls ``setsid()`` which
    detaches from any inherited controlling tty. On macOS/BSD the first
    ``open()`` of a tty by a session leader does NOT auto-acquire it as
    ctty — we must explicitly ioctl TIOCSCTTY. Without this, the kernel
    has no foreground process group to signal on ``TIOCSWINSZ``, so
    ``SIGWINCH`` never reaches the child and TUIs (Claude Code, vim, etc.)
    don't re-render on resize.
    """
    with contextlib.suppress(OSError):
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def set_winsize(fd: int, cols: int, rows: int) -> None:
    """Set the PTY window size via TIOCSWINSZ."""
    packed = struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)
