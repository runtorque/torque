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

# Matches OSC7 cwd reports emitted by our zsh/bash prompt hooks.
OSC7_RE = re.compile("\x1b]7;file://[^/\x07\x1b]*(/.*?)(?:\x07|\x1b\\\\)")

# Matches ANSI escape sequences so we can strip them out of screen text.
ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1B\\))",
    re.DOTALL,
)

Utf8IncrementalDecoder = codecs.getincrementaldecoder("utf-8")


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
