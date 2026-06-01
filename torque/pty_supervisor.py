"""Standalone-mode PTY supervisor sidecar.

This process owns every PTY master FD and its child subprocess in Torque's
standalone mode. The Torque daemon connects over a local unix-domain
socket and exchanges length-prefixed JSON frames to create, write to,
resize, subscribe to, list, and close sessions.

The supervisor is spawned with ``start_new_session=True`` and intended
to outlive the Torque daemon across ``os.execv`` restarts or daemon
crashes: if the daemon goes away, sessions keep running; when a new
daemon starts, it pings the existing socket and re-subscribes to
existing sessions.

Scope: standalone PTY sessions. The foreground daemon owns its sessions through
the supervised PTY adapter while this helper isolates child processes across
daemon restarts.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import errno
import fcntl
import json
import logging
import os
import pty
import select
import signal
import socket
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .pty_core import (
    BUFFER_LIMIT,
    Utf8IncrementalDecoder,
    preexec_acquire_ctty,
    set_winsize,
)

PROTOCOL_VERSION = 1  # started_at/list-supervisor metadata is additive.
DEFAULT_SOCKET_NAME = "pty_supervisor.sock"
DEFAULT_PID_FILE_NAME = "pty_supervisor.pid"
DEFAULT_LOG_FILE_NAME = "pty_supervisor.log"
ADOPT_STATE_VERSION = 1
ADOPT_STATE_PREFIX = "pty_supervisor_adopt"
DEFAULT_RESTART_TIMEOUT_SECONDS = 10.0

# Max JSON frame size to accept before rejecting as protocol error.
# Snapshot replay can be large (up to BUFFER_LIMIT bytes base64-encoded).
MAX_FRAME_BYTES = 2 * BUFFER_LIMIT + 4096

# Upper bound on a single write to a PTY master. The master fd is non-blocking
# so a child that has stopped draining its stdin makes os.write raise EAGAIN
# rather than block; we wait (via select) up to this deadline for the input
# buffer to drain, then report backpressure instead of stalling the shared
# supervisor connection forever (which used to wedge every other session).
WRITE_DEADLINE_SECONDS = 5.0

# Poll cadence for the readable wait so the read thread periodically rechecks
# session liveness even if no bytes and no EOF arrive.
READ_POLL_INTERVAL_SECONDS = 1.0

log = logging.getLogger("torque.pty_supervisor")


def _select_read_chunk(fd: int, closed: "Callable[[], bool]") -> bytes:
    """Block (in a worker thread) until the non-blocking master ``fd`` is
    readable, then read one chunk.

    Returns ``b""`` on EOF / closed fd so the read loop terminates. Raises
    ``OSError`` only for genuinely unexpected errors.
    """
    while not closed():
        try:
            readable, _, _ = select.select([fd], [], [], READ_POLL_INTERVAL_SECONDS)
        except (OSError, ValueError):
            return b""  # fd closed underneath us
        if not readable:
            continue
        try:
            return os.read(fd, 4096)
        except BlockingIOError:
            continue
        except OSError as exc:
            if exc.errno in (errno.EIO, errno.EBADF):
                return b""
            raise
    return b""


def _bounded_pty_write(fd: int, data: bytes, deadline: float) -> int:
    """Write ``data`` to the non-blocking master ``fd``, bounded by a monotonic
    ``deadline``.

    Runs in a worker thread and always returns within the deadline (it waits
    for writability via ``select`` with the remaining budget, never a bare
    blocking ``os.write``), so a full PTY input buffer can't pin the thread.
    Returns the number of bytes written; a short count means the buffer stayed
    full past the deadline (backpressure).
    """
    view = memoryview(data)
    written = 0
    while written < len(data):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            _, writable, _ = select.select([], [fd], [], remaining)
        except (OSError, ValueError):
            break
        if not writable:
            continue
        try:
            written += os.write(fd, view[written:])
        except BlockingIOError:
            continue
    return written


def _set_fd_cloexec(fd: int, cloexec: bool) -> None:
    """Set/clear FD_CLOEXEC on ``fd`` explicitly.

    Python usually creates new descriptors non-inheritable by default, but the
    restart path relies on very specific inheritance semantics: PTY masters
    must survive ``execv`` while supervisor listener/state-file descriptors
    must not. Keep that policy visible and testable instead of relying on
    platform defaults.
    """
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    if cloexec:
        flags |= fcntl.FD_CLOEXEC
    else:
        flags &= ~fcntl.FD_CLOEXEC
    fcntl.fcntl(fd, fcntl.F_SETFD, flags)


def _fd_cloexec(fd: int) -> bool:
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    return bool(flags & fcntl.FD_CLOEXEC)


def _fd_alive(fd: int) -> bool:
    try:
        fcntl.fcntl(fd, fcntl.F_GETFD)
        os.fstat(fd)
    except OSError:
        return False
    return True


def _fd_child_matches(fd: int, pid: int) -> bool:
    """Best-effort PTY foreground-pgrp validation.

    Some platforms do not expose a meaningful foreground process group on the
    PTY master side; unsupported probes are treated as "unknown but not a
    failure". When the kernel does answer, require it to match the child pgid.
    """
    try:
        child_pgid = os.getpgid(pid)
        tty_pgrp = os.tcgetpgrp(fd)
    except (AttributeError, OSError, ProcessLookupError):
        return True
    return tty_pgrp <= 0 or child_pgid <= 0 or tty_pgrp == child_pgid


def _set_server_sockets_cloexec(server: asyncio.AbstractServer) -> None:
    for sock in list(server.sockets or []):
        try:
            _set_fd_cloexec(sock.fileno(), True)
        except OSError:
            log.debug("Failed to set FD_CLOEXEC on listener", exc_info=True)


def _atomic_write_json(path: Path, payload: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = os.open(tmp, flags, 0o600)
    try:
        # Explicit even when O_CLOEXEC exists; this is a guardrail, not an
        # optimization.
        _set_fd_cloexec(fd, True)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            json.dump(payload, fh, separators=(",", ":"), sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        with contextlib.suppress(OSError):
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except Exception:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    return path


def _close_inherited_pty_fds(except_fds: set[int] | None = None) -> int:
    """Close inherited PTY-like descriptors not adopted into a session.

    This is a clean-fallback safety net for corrupt/missing adopt state. It
    deliberately targets only tty descriptors so the fresh event loop/listener
    descriptors are not disturbed.
    """
    keep = set(except_fds or set())
    try:
        limit = int(os.sysconf("SC_OPEN_MAX"))
    except (OSError, ValueError, AttributeError):
        limit = 1024
    limit = max(3, min(limit, 4096))
    closed = 0
    for fd in range(3, limit):
        if fd in keep:
            continue
        try:
            is_tty = os.isatty(fd)
        except OSError:
            continue
        if not is_tty:
            continue
        with contextlib.suppress(OSError):
            os.close(fd)
            closed += 1
    return closed


# -- Wire framing ----------------------------------------------------------


async def read_frame(reader: asyncio.StreamReader) -> Optional[dict]:
    """Read one length-prefixed JSON frame. Returns None on EOF."""
    try:
        header = await reader.readexactly(4)
    except asyncio.IncompleteReadError:
        return None
    length = struct.unpack(">I", header)[0]
    if length == 0:
        return {}
    if length > MAX_FRAME_BYTES:
        raise ValueError(f"frame too large: {length} bytes")
    try:
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        return None
    return json.loads(body.decode("utf-8"))


async def write_frame(writer: asyncio.StreamWriter, message: dict) -> None:
    """Write one length-prefixed JSON frame."""
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise ValueError(f"frame too large: {len(body)} bytes")
    writer.write(struct.pack(">I", len(body)) + body)
    await writer.drain()


# -- Session registry ------------------------------------------------------


@dataclass
class SupervisorSession:
    session_id: str
    cell_id: str
    pid: int
    master_fd: int
    shell_argv: list
    cwd: str
    cols: int
    rows: int
    bootstrap_dir: str = ""
    started_at: float = 0.0
    buffer: bytearray = field(default_factory=bytearray)
    total_bytes: int = 0
    subscribers: set = field(default_factory=set)
    read_task: Optional[asyncio.Task] = None
    process: Optional[asyncio.subprocess.Process] = None
    closed: bool = False
    exit_status: Optional[int] = None
    detaching_for_exec: bool = False

    def snapshot(self) -> dict:
        return {
            "session_id": self.session_id,
            "cell_id": self.cell_id,
            "pid": self.pid,
            "alive": not self.closed,
            "cols": self.cols,
            "rows": self.rows,
            "total_bytes": self.total_bytes,
            "bootstrap_dir": self.bootstrap_dir,
            "started_at": float(self.started_at or 0.0),
            "shell_argv": list(self.shell_argv),
            "cwd": self.cwd,
        }


@dataclass
class SupervisorMetrics:
    ops_total: dict[str, int] = field(default_factory=dict)
    errors_total: dict[str, int] = field(default_factory=dict)
    bytes_written: int = 0
    bytes_read: int = 0
    sessions_current: int = 0
    sessions_peak: int = 0
    sessions_created_total: int = 0
    read_loop_failures: int = 0
    write_deadline_hits: int = 0

    def record_op(self, op: str) -> None:
        key = str(op or "<missing>")
        self.ops_total[key] = int(self.ops_total.get(key, 0) or 0) + 1

    def record_error(self, code: str) -> None:
        key = str(code or "unknown")
        self.errors_total[key] = int(self.errors_total.get(key, 0) or 0) + 1

    def record_session_created(self, current: int) -> None:
        self.sessions_created_total += 1
        self.record_sessions_current(current)

    def record_sessions_current(self, current: int) -> None:
        current = max(0, int(current or 0))
        self.sessions_current = current
        self.sessions_peak = max(self.sessions_peak, current)

    def snapshot(self) -> dict:
        return {
            "ops_total": dict(self.ops_total),
            "errors_total": dict(self.errors_total),
            "bytes_written": int(self.bytes_written),
            "bytes_read": int(self.bytes_read),
            "sessions_current": int(self.sessions_current),
            "sessions_peak": int(self.sessions_peak),
            "sessions_created_total": int(self.sessions_created_total),
            "read_loop_failures": int(self.read_loop_failures),
            "write_deadline_hits": int(self.write_deadline_hits),
        }


# -- Supervisor ------------------------------------------------------------


class PtySupervisor:
    """In-process supervisor. Also used directly by tests."""

    def __init__(
        self,
        *,
        data_dir: Optional[Path] = None,
        restart_callback=None,
        restart_epoch: int = 0,
        restart_nonce: str = "",
        last_restart: Optional[dict] = None,
    ):
        self.sessions: dict[str, SupervisorSession] = {}
        self.started_at = time.time()
        self._lock = asyncio.Lock()
        self.metrics = SupervisorMetrics()
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.restart_callback = restart_callback
        self.restart_epoch = int(restart_epoch or 0)
        self.restart_nonce = str(restart_nonce or "")
        self.last_restart = dict(last_restart or {})
        self._restarting = False
        self._client_writers: set[asyncio.StreamWriter] = set()

    def supervisor_snapshot(self) -> dict:
        return {
            "pid": os.getpid(),
            "started_at": float(self.started_at or 0.0),
            "restart_epoch": int(self.restart_epoch or 0),
            "restart_nonce": self.restart_nonce,
            "last_restart": dict(self.last_restart or {}),
        }

    def _live_session_count(self) -> int:
        return sum(1 for session in self.sessions.values()
                   if not session.closed)

    def _record_sessions_current(self) -> None:
        self.metrics.record_sessions_current(self._live_session_count())

    async def _write_error(
        self,
        writer: asyncio.StreamWriter,
        code: str,
        message: str,
    ) -> None:
        self.metrics.record_error(code)
        await write_frame(writer, {
            "type": "error",
            "code": code,
            "message": message,
        })

    # -- client connections ------------------------------------------------

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        subs: set[str] = set()
        peer = writer.get_extra_info("peername") or writer.get_extra_info("sockname")
        self._client_writers.add(writer)
        log.info("Client connected: %s", peer)
        try:
            while True:
                try:
                    msg = await read_frame(reader)
                except ValueError as exc:
                    self.metrics.record_error("protocol_error")
                    await _safe_write(writer, {
                        "type": "error",
                        "code": "protocol_error",
                        "message": str(exc),
                    })
                    break
                except (ConnectionResetError, BrokenPipeError):
                    break
                except Exception:
                    log.exception("Read-frame failed")
                    break
                if msg is None:
                    break
                try:
                    await self._dispatch(msg, writer, subs)
                except (ConnectionResetError, BrokenPipeError):
                    break
                except Exception as exc:
                    log.exception("Dispatch failed for op=%s", msg.get("op"))
                    self.metrics.record_error("internal_error")
                    await _safe_write(writer, {
                        "type": "error",
                        "code": "internal_error",
                        "message": str(exc),
                    })
        finally:
            self._client_writers.discard(writer)
            for sid in list(subs):
                sess = self.sessions.get(sid)
                if sess:
                    sess.subscribers.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
            log.info("Client disconnected: %s", peer)

    async def _dispatch(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
        subs: set,
    ) -> None:
        op = str(msg.get("op", "") or "")
        self.metrics.record_op(op)
        if self._restarting and op not in {"ping", "list", "metrics"}:
            await self._write_error(
                writer,
                "restarting",
                "PTY supervisor is restarting; retry after reconnect.",
            )
            return
        if op == "ping":
            await write_frame(writer, {
                "type": "pong",
                "version": PROTOCOL_VERSION,
                "pid": os.getpid(),
                "started_at": float(self.started_at or 0.0),
                "restart_epoch": int(self.restart_epoch or 0),
                "restart_nonce": self.restart_nonce,
                "last_restart": dict(self.last_restart or {}),
            })
        elif op == "metrics":
            self._record_sessions_current()
            await write_frame(writer, {
                "type": "metrics",
                "metrics": self.metrics.snapshot(),
            })
        elif op == "restart":
            await self._op_restart(msg, writer)
        elif op == "create":
            await self._op_create(msg, writer)
        elif op == "write":
            await self._op_write(msg, writer)
        elif op == "resize":
            await self._op_resize(msg, writer)
        elif op == "close":
            await self._op_close(msg, writer)
        elif op == "list":
            await write_frame(writer, {
                "type": "list",
                "supervisor": self.supervisor_snapshot(),
                "sessions": [s.snapshot() for s in self.sessions.values()],
            })
        elif op == "subscribe":
            await self._op_subscribe(msg, writer, subs)
        elif op == "unsubscribe":
            await self._op_unsubscribe(msg, writer, subs)
        else:
            await self._write_error(
                writer, "protocol_error", f"unknown op: {op!r}")

    # -- ops ---------------------------------------------------------------

    def _restart_state_path(self, restart_nonce: str) -> Path:
        if self.data_dir is None:
            raise RuntimeError("restart requires supervisor data_dir")
        safe_nonce = "".join(
            ch for ch in str(restart_nonce or "") if ch.isalnum() or ch in "-_"
        ) or uuid.uuid4().hex
        return (
            Path(self.data_dir)
            / f"{ADOPT_STATE_PREFIX}_{os.getpid()}_{safe_nonce}.json"
        )

    def _write_adopt_state(
        self,
        *,
        restart_epoch: int,
        restart_nonce: str,
    ) -> Path:
        path = self._restart_state_path(restart_nonce)
        sessions = []
        for session in self.sessions.values():
            if session.closed:
                continue
            _set_fd_cloexec(session.master_fd, False)
            sessions.append({
                "session_id": session.session_id,
                "cell_id": session.cell_id,
                "master_fd": int(session.master_fd),
                "pid": int(session.pid),
                "cols": int(session.cols),
                "rows": int(session.rows),
                "cwd": session.cwd,
                "shell_argv": list(session.shell_argv),
                "bootstrap_dir": session.bootstrap_dir,
                "started_at": float(session.started_at or 0.0),
                "total_bytes": int(session.total_bytes or 0),
            })
        payload = {
            "version": ADOPT_STATE_VERSION,
            "created_at": time.time(),
            "supervisor_pid": os.getpid(),
            "restart_epoch": int(restart_epoch or 0),
            "restart_nonce": str(restart_nonce or ""),
            "sessions": sessions,
        }
        return _atomic_write_json(path, payload)

    async def _op_restart(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
    ) -> None:
        del msg
        if self.restart_callback is None or self.data_dir is None:
            await self._write_error(
                writer,
                "restart_unavailable",
                "PTY supervisor restart is unavailable in this harness.",
            )
            return
        if self._restarting:
            await self._write_error(
                writer,
                "restart_in_progress",
                "PTY supervisor restart is already in progress.",
            )
            return
        restart_epoch = int(self.restart_epoch or 0) + 1
        restart_nonce = uuid.uuid4().hex
        self._restarting = True
        try:
            state_path = self._write_adopt_state(
                restart_epoch=restart_epoch,
                restart_nonce=restart_nonce,
            )
        except Exception as exc:
            self._restarting = False
            log.exception("Failed to prepare PTY supervisor restart")
            await self._write_error(
                writer,
                "restart_prepare_failed",
                str(exc),
            )
            return
        live_count = self._live_session_count()
        try:
            await write_frame(writer, {
                "type": "ok",
                "op": "restart",
                "restart_epoch": restart_epoch,
                "restart_nonce": restart_nonce,
                "sessions": live_count,
                "adopt_state": str(state_path),
            })
        except Exception:
            # The supervisor is still fully alive and has not detached or
            # exec'd yet. Roll the prepared restart back instead of leaving the
            # process stuck in the mutating-op rejection gate forever.
            self._restarting = False
            with contextlib.suppress(OSError):
                state_path.unlink()
            log.warning(
                "Failed to deliver PTY supervisor restart ack; "
                "prepared restart rolled back",
                exc_info=True,
            )
            return
        log.info(
            "Prepared PTY supervisor restart epoch=%s nonce=%s sessions=%s",
            restart_epoch,
            restart_nonce,
            live_count,
        )
        asyncio.create_task(self.restart_callback(
            state_path,
            restart_epoch,
            restart_nonce,
            live_count,
        ))

    async def _op_create(self, msg: dict, writer: asyncio.StreamWriter) -> None:
        session_id = str(msg.get("session_id") or "").strip()
        cell_id = str(msg.get("cell_id") or "").strip()
        shell_argv = msg.get("shell_argv") or []
        env = msg.get("env") or {}
        cwd = str(msg.get("cwd") or "") or None
        cols = int(msg.get("cols") or 120)
        rows = int(msg.get("rows") or 32)
        bootstrap_dir = str(msg.get("bootstrap_dir") or "")
        if not session_id or not shell_argv:
            await self._write_error(
                writer,
                "protocol_error",
                "create requires session_id and shell_argv",
            )
            return
        if session_id in self.sessions:
            await self._write_error(
                writer, "session_exists", session_id)
            return
        try:
            session = await self._spawn(
                session_id=session_id,
                cell_id=cell_id,
                shell_argv=list(shell_argv),
                env={str(k): str(v) for k, v in env.items()},
                cwd=cwd,
                cols=cols,
                rows=rows,
                bootstrap_dir=bootstrap_dir,
            )
        except Exception as exc:
            log.exception("Failed to spawn session %s", session_id)
            await self._write_error(
                writer, "shell_spawn_failed", str(exc))
            return
        self.sessions[session_id] = session
        self.metrics.record_session_created(self._live_session_count())
        session.read_task = asyncio.create_task(self._read_loop(session))
        await write_frame(writer, {
            "type": "ok",
            "op": "create",
            "session_id": session_id,
            "pid": session.pid,
            "started_at": float(session.started_at or 0.0),
        })

    async def _op_write(self, msg: dict, writer: asyncio.StreamWriter) -> None:
        session_id = str(msg.get("session_id") or "")
        data_b64 = msg.get("data") or ""
        sess = self.sessions.get(session_id)
        if not sess or sess.closed:
            await self._write_error(
                writer, "unknown_session", session_id)
            return
        try:
            payload = base64.b64decode(data_b64) if data_b64 else b""
        except Exception as exc:
            await self._write_error(
                writer, "protocol_error", f"bad base64: {exc}")
            return
        if payload:
            deadline = time.monotonic() + WRITE_DEADLINE_SECONDS
            try:
                written = await asyncio.to_thread(
                    _bounded_pty_write, sess.master_fd, payload, deadline)
            except OSError as exc:
                await self._write_error(
                    writer, "write_failed", str(exc))
                return
            self.metrics.bytes_written += int(written or 0)
            if written < len(payload):
                # The child stopped draining stdin and its PTY input buffer
                # stayed full past the deadline. Report backpressure rather
                # than blocking this connection (and thus every other
                # session's ops) on a single wedged agent.
                self.metrics.write_deadline_hits += 1
                await self._write_error(
                    writer,
                    "write_backpressure",
                    (
                        f"wrote {written}/{len(payload)} bytes before "
                        f"{WRITE_DEADLINE_SECONDS:.0f}s deadline"
                    ),
                )
                return
        await write_frame(writer, {"type": "ok", "op": "write"})

    async def _op_resize(self, msg: dict, writer: asyncio.StreamWriter) -> None:
        session_id = str(msg.get("session_id") or "")
        cols = max(1, int(msg.get("cols") or 0))
        rows = max(1, int(msg.get("rows") or 0))
        sess = self.sessions.get(session_id)
        if not sess or sess.closed:
            await self._write_error(
                writer, "unknown_session", session_id)
            return
        sess.cols = cols
        sess.rows = rows
        set_winsize(sess.master_fd, cols, rows)
        await write_frame(writer, {"type": "ok", "op": "resize"})

    async def _op_close(self, msg: dict, writer: asyncio.StreamWriter) -> None:
        session_id = str(msg.get("session_id") or "")
        sess = self.sessions.get(session_id)
        if not sess:
            await self._write_error(
                writer, "unknown_session", session_id)
            return
        await self._terminate_session(sess)
        await write_frame(writer, {"type": "ok", "op": "close"})

    async def _op_subscribe(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
        subs: set,
    ) -> None:
        session_id = str(msg.get("session_id") or "")
        sess = self.sessions.get(session_id)
        if not sess:
            await self._write_error(
                writer, "unknown_session", session_id)
            return
        sess.subscribers.add(writer)
        subs.add(session_id)
        # Acknowledge first so the client's response path stays simple.
        await write_frame(writer, {
            "type": "ok",
            "op": "subscribe",
            "session_id": session_id,
        })
        # Then stream the buffer tail as a snapshot frame.
        await write_frame(writer, {
            "type": "snapshot",
            "session_id": session_id,
            "cell_id": sess.cell_id,
            "pid": sess.pid,
            "alive": not sess.closed,
            "cols": sess.cols,
            "rows": sess.rows,
            "total_bytes": sess.total_bytes,
            "started_at": float(sess.started_at or 0.0),
            "data": base64.b64encode(bytes(sess.buffer)).decode("ascii"),
        })
        if sess.closed and sess.exit_status is not None:
            await write_frame(writer, {
                "type": "exit",
                "session_id": session_id,
                "exit_status": sess.exit_status,
            })

    async def _op_unsubscribe(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
        subs: set,
    ) -> None:
        session_id = str(msg.get("session_id") or "")
        sess = self.sessions.get(session_id)
        if sess:
            sess.subscribers.discard(writer)
        subs.discard(session_id)
        await write_frame(writer, {"type": "ok", "op": "unsubscribe"})

    # -- process management ------------------------------------------------

    async def _spawn(
        self,
        *,
        session_id: str,
        cell_id: str,
        shell_argv: list,
        env: dict,
        cwd: Optional[str],
        cols: int,
        rows: int,
        bootstrap_dir: str,
    ) -> SupervisorSession:
        master_fd, slave_fd = pty.openpty()
        # The supervisor side (master) is non-blocking so reads/writes are
        # readiness-gated via select; the child keeps the blocking slave fd and
        # behaves normally.
        os.set_blocking(master_fd, False)
        set_winsize(master_fd, cols, rows)
        try:
            process = await asyncio.create_subprocess_exec(
                *shell_argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd or None,
                env=env,
                start_new_session=True,
                preexec_fn=preexec_acquire_ctty,
            )
        finally:
            with contextlib.suppress(OSError):
                os.close(slave_fd)
        return SupervisorSession(
            session_id=session_id,
            cell_id=cell_id,
            pid=process.pid,
            master_fd=master_fd,
            shell_argv=list(shell_argv),
            cwd=cwd or "",
            cols=cols,
            rows=rows,
            bootstrap_dir=bootstrap_dir,
            started_at=time.time(),
            process=process,
        )

    async def _read_loop(self, session: SupervisorSession) -> None:
        try:
            while True:
                try:
                    chunk = await asyncio.to_thread(
                        _select_read_chunk,
                        session.master_fd,
                        lambda: session.closed or session.detaching_for_exec,
                    )
                except OSError as exc:
                    if exc.errno in (errno.EIO, errno.EBADF):
                        break
                    raise
                if not chunk:
                    break
                session.total_bytes += len(chunk)
                self.metrics.bytes_read += len(chunk)
                session.buffer.extend(chunk)
                if len(session.buffer) > BUFFER_LIMIT:
                    del session.buffer[:len(session.buffer) - BUFFER_LIMIT]
                await self._broadcast(session, chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.metrics.read_loop_failures += 1
            log.exception("Read loop failed for %s", session.session_id)
        finally:
            if not session.detaching_for_exec:
                await self._finalize_session(session)

    async def _broadcast(
        self,
        session: SupervisorSession,
        chunk: bytes,
    ) -> None:
        if not session.subscribers:
            return
        payload = {
            "type": "output",
            "session_id": session.session_id,
            "data": base64.b64encode(chunk).decode("ascii"),
            "byte_offset": session.total_bytes,
        }
        dead = []
        for sub in list(session.subscribers):
            try:
                await write_frame(sub, payload)
            except Exception:
                dead.append(sub)
        for sub in dead:
            session.subscribers.discard(sub)

    async def _finalize_session(self, session: SupervisorSession) -> None:
        if session.closed:
            return
        session.closed = True
        self._record_sessions_current()
        exit_status: Optional[int] = None
        if session.process is not None:
            try:
                exit_status = await asyncio.wait_for(
                    session.process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(
                        ProcessLookupError, PermissionError, OSError):
                    os.killpg(session.pid, signal.SIGTERM)
                try:
                    exit_status = await asyncio.wait_for(
                        session.process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    with contextlib.suppress(
                            ProcessLookupError, PermissionError, OSError):
                        os.killpg(session.pid, signal.SIGKILL)
                    with contextlib.suppress(Exception):
                        exit_status = await session.process.wait()
        else:
            # Fallback path for sessions without an ``asyncio`` Process
            # handle (adopted from a previous daemon image). Poll for
            # reap status with a short bounded retry.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                try:
                    reaped_pid, status = os.waitpid(
                        session.pid, os.WNOHANG)
                except (ProcessLookupError, ChildProcessError):
                    break
                if reaped_pid == session.pid:
                    exit_status = status
                    break
                await asyncio.sleep(0.05)
        session.exit_status = (
            int(exit_status) if exit_status is not None else None)
        with contextlib.suppress(OSError):
            os.close(session.master_fd)
        # Broadcast exit to every subscriber before dropping them.
        exit_msg = {
            "type": "exit",
            "session_id": session.session_id,
            "exit_status": session.exit_status,
        }
        for sub in list(session.subscribers):
            with contextlib.suppress(Exception):
                await write_frame(sub, exit_msg)
        session.subscribers.clear()
        # Keep the session in the registry briefly so late `list`/
        # `subscribe` calls see the exit. Drop it after a short delay.
        asyncio.create_task(self._drop_after(session.session_id, 5.0))

    async def _drop_after(self, session_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        self.sessions.pop(session_id, None)

    async def _terminate_session(self, session: SupervisorSession) -> None:
        if session.closed:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(session.pid, signal.SIGHUP)
        # The read loop will observe EIO and call _finalize_session.
        try:
            await asyncio.wait_for(self._wait_closed(session), timeout=1.5)
        except asyncio.TimeoutError:
            with contextlib.suppress(
                    ProcessLookupError, PermissionError, OSError):
                os.killpg(session.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self._wait_closed(session), timeout=1.5)
            except asyncio.TimeoutError:
                with contextlib.suppress(
                        ProcessLookupError, PermissionError, OSError):
                    os.killpg(session.pid, signal.SIGKILL)

    async def _wait_closed(self, session: SupervisorSession) -> None:
        while not session.closed:
            await asyncio.sleep(0.05)

    async def shutdown(self) -> None:
        for session in list(self.sessions.values()):
            await self._terminate_session(session)

    async def detach_for_exec(self) -> None:
        """Stop supervisor asyncio work without terminating PTY children.

        This is the critical restart path: do not call ``shutdown`` or
        ``_terminate_session``. Children and master fds must remain alive until
        ``os.execv`` replaces this process image and adopts the inherited fds.
        """
        for session in list(self.sessions.values()):
            if session.closed:
                continue
            session.detaching_for_exec = True
            session.subscribers.clear()
            if session.read_task and not session.read_task.done():
                session.read_task.cancel()
        for session in list(self.sessions.values()):
            task = session.read_task
            if task and not task.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            session.read_task = None
        for writer in list(self._client_writers):
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
        self._client_writers.clear()

    def _adopt_report(
        self,
        *,
        restart_epoch: int,
        restart_nonce: str,
        expected_sessions: int,
    ) -> dict:
        return {
            "attempted": True,
            "version": ADOPT_STATE_VERSION,
            "restart_epoch": int(restart_epoch or 0),
            "restart_nonce": str(restart_nonce or ""),
            "expected_sessions": max(0, int(expected_sessions or 0)),
            "adopted_sessions": 0,
            "lost_sessions": 0,
            "lost_session_ids": [],
            "failed_sessions": [],
            "errors": [],
            "clean_fallback": False,
        }

    def _validate_adopt_entry(
        self,
        raw: dict,
        *,
        seen_session_ids: set[str],
        seen_fds: set[int],
    ) -> tuple[SupervisorSession | None, str]:
        try:
            session_id = str(raw.get("session_id") or "").strip()
            cell_id = str(raw.get("cell_id") or "").strip()
            master_fd = int(raw.get("master_fd"))
            pid = int(raw.get("pid"))
            cols = max(1, int(raw.get("cols") or 120))
            rows = max(1, int(raw.get("rows") or 32))
        except Exception as exc:
            return None, f"bad entry shape: {exc}"
        if not session_id:
            return None, "missing session_id"
        if session_id in seen_session_ids:
            return None, f"duplicate session_id {session_id}"
        if master_fd < 0:
            return None, f"invalid master_fd {master_fd}"
        if master_fd in seen_fds:
            return None, f"duplicate master_fd {master_fd}"
        if pid <= 0:
            return None, f"invalid child pid {pid}"
        if not _fd_alive(master_fd):
            return None, f"master_fd {master_fd} is not open"
        if not _pid_alive(pid):
            return None, f"child pid {pid} is not alive"
        if not _fd_child_matches(master_fd, pid):
            return None, (
                f"master_fd {master_fd} foreground process group does "
                f"not match child pid {pid}"
            )
        try:
            os.set_blocking(master_fd, False)
            _set_fd_cloexec(master_fd, False)
        except OSError as exc:
            return None, f"failed to prepare master_fd {master_fd}: {exc}"
        session = SupervisorSession(
            session_id=session_id,
            cell_id=cell_id,
            pid=pid,
            master_fd=master_fd,
            shell_argv=list(raw.get("shell_argv") or []),
            cwd=str(raw.get("cwd") or ""),
            cols=cols,
            rows=rows,
            bootstrap_dir=str(raw.get("bootstrap_dir") or ""),
            started_at=float(raw.get("started_at") or 0.0),
            total_bytes=max(0, int(raw.get("total_bytes") or 0)),
            process=None,
        )
        return session, ""

    async def adopt_from_state(
        self,
        adopt_state: Path,
        *,
        restart_epoch: int = 0,
        restart_nonce: str = "",
        expected_sessions: int = 0,
    ) -> dict:
        """Rebuild sessions around inherited PTY master fds.

        Invalid/corrupt state never prevents the supervisor from serving: this
        method records a loud report and falls back to a clean supervisor.
        """
        report = self._adopt_report(
            restart_epoch=restart_epoch,
            restart_nonce=restart_nonce,
            expected_sessions=expected_sessions,
        )
        path = Path(adopt_state)
        payload = None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            report["errors"].append(f"failed to read adopt state: {exc}")
            report["clean_fallback"] = True
            report["lost_sessions"] = report["expected_sessions"]
            closed = _close_inherited_pty_fds()
            if closed:
                report["closed_unadopted_fds"] = closed
            self.last_restart = report
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            log.error(
                "PTY supervisor adopt failed; clean fallback. report=%s",
                report,
            )
            return report

        if not isinstance(payload, dict):
            report["errors"].append("adopt state root is not an object")
            report["clean_fallback"] = True
        elif int(payload.get("version") or 0) != ADOPT_STATE_VERSION:
            report["errors"].append(
                f"unsupported adopt state version {payload.get('version')!r}")
            report["clean_fallback"] = True

        if report["clean_fallback"]:
            report["lost_sessions"] = report["expected_sessions"]
            closed = _close_inherited_pty_fds()
            if closed:
                report["closed_unadopted_fds"] = closed
            self.last_restart = report
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            log.error(
                "PTY supervisor adopt rejected state; clean fallback. "
                "report=%s",
                report,
            )
            return report

        state_epoch = int(payload.get("restart_epoch") or restart_epoch or 0)
        state_nonce = str(payload.get("restart_nonce") or restart_nonce or "")
        self.restart_epoch = state_epoch
        self.restart_nonce = state_nonce
        report["restart_epoch"] = state_epoch
        report["restart_nonce"] = state_nonce
        raw_sessions = list(payload.get("sessions") or [])
        if not report["expected_sessions"]:
            report["expected_sessions"] = len(raw_sessions)

        seen_ids: set[str] = set()
        seen_fds: set[int] = set()
        adopted_fds: set[int] = set()
        for raw in raw_sessions:
            if not isinstance(raw, dict):
                report["errors"].append("session entry is not an object")
                report["failed_sessions"].append({
                    "session_id": "",
                    "reason": "entry is not an object",
                })
                continue
            session, error = self._validate_adopt_entry(
                raw,
                seen_session_ids=seen_ids,
                seen_fds=seen_fds,
            )
            sid = str(raw.get("session_id") or "")
            if error or session is None:
                report["failed_sessions"].append({
                    "session_id": sid,
                    "reason": error or "validation failed",
                })
                if sid:
                    report["lost_session_ids"].append(sid)
                with contextlib.suppress(Exception):
                    fd = int(raw.get("master_fd"))
                    if fd >= 3 and _fd_alive(fd):
                        os.close(fd)
                continue
            seen_ids.add(session.session_id)
            seen_fds.add(session.master_fd)
            adopted_fds.add(session.master_fd)
            self.sessions[session.session_id] = session

        report["adopted_sessions"] = len(self.sessions)
        lost = max(0, report["expected_sessions"] - report["adopted_sessions"])
        report["lost_sessions"] = max(lost, len(report["lost_session_ids"]))
        if report["lost_sessions"]:
            log.error("PTY supervisor restart lost sessions: %s", report)
        else:
            log.info("PTY supervisor restart adopted sessions: %s", report)
        self._record_sessions_current()
        for session in list(self.sessions.values()):
            session.read_task = asyncio.create_task(self._read_loop(session))
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        self.last_restart = report
        return report


async def _safe_write(writer: asyncio.StreamWriter, message: dict) -> None:
    try:
        await write_frame(writer, message)
    except (ConnectionResetError, BrokenPipeError):
        return
    except Exception:
        log.exception("Failed to write frame")


# -- Lifecycle: detached spawn + ensure_running ----------------------------


def _paths(data_dir: Path) -> dict:
    return {
        "socket": data_dir / DEFAULT_SOCKET_NAME,
        "pid": data_dir / DEFAULT_PID_FILE_NAME,
        "log": data_dir / DEFAULT_LOG_FILE_NAME,
    }


def _request_socket(
    socket_path: Path,
    op: str,
    *,
    timeout: float = 1.0,
) -> Optional[dict]:
    """Blocking unix-socket request. Returns one response payload on success."""
    if not socket_path.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
    except OSError:
        return None
    try:
        body = json.dumps({"op": str(op or "")}).encode("utf-8")
        sock.sendall(struct.pack(">I", len(body)) + body)
        header = _recv_exact(sock, 4, timeout)
        if not header:
            return None
        length = struct.unpack(">I", header)[0]
        if length == 0 or length > MAX_FRAME_BYTES:
            return None
        resp_body = _recv_exact(sock, length, timeout)
        if not resp_body:
            return None
        return json.loads(resp_body.decode("utf-8"))
    except Exception:
        return None
    finally:
        with contextlib.suppress(Exception):
            sock.close()


def _ping_socket(socket_path: Path, timeout: float = 1.0) -> Optional[dict]:
    """Blocking unix-socket ping. Returns pong payload on success."""
    return _request_socket(socket_path, "ping", timeout=timeout)


def _metrics_socket(socket_path: Path, timeout: float = 1.0) -> Optional[dict]:
    """Blocking unix-socket metrics request. Returns metrics payload."""
    return _request_socket(socket_path, "metrics", timeout=timeout)


def _recv_exact(sock: socket.socket, n: int, timeout: float) -> Optional[bytes]:
    sock.settimeout(timeout)
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _read_pid_file(pid_path: Path) -> Optional[int]:
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _cleanup_stale(paths: dict) -> None:
    with contextlib.suppress(FileNotFoundError):
        paths["socket"].unlink()
    with contextlib.suppress(FileNotFoundError):
        paths["pid"].unlink()


def spawn_detached(data_dir: Path) -> int:
    """Launch a detached supervisor process. Returns the child PID.

    Uses ``start_new_session=True`` so the child outlives the daemon's
    session; ``close_fds=True`` so the child doesn't inherit daemon FDs.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = _paths(data_dir)
    log_fh = open(paths["log"], "ab", buffering=0)
    try:
        env = os.environ.copy()
        # Ensure the child can import ``torque.*``. The daemon's entrypoint
        # inserts SCRIPT_DIR into sys.path; propagate it via PYTHONPATH
        # so ``python -m torque.pty_supervisor`` resolves.
        pkg_root = Path(__file__).resolve().parent.parent
        existing = env.get("PYTHONPATH", "")
        if str(pkg_root) not in existing.split(os.pathsep):
            env["PYTHONPATH"] = (
                str(pkg_root) + (os.pathsep + existing if existing else "")
            )
        argv = [
            sys.executable,
            "-m",
            "torque.pty_supervisor",
            "--data-dir",
            str(data_dir),
        ]
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
            close_fds=True,
            env=env,
        )
    finally:
        log_fh.close()
    return proc.pid


def ensure_running(data_dir: Path, *, timeout: float = 3.0) -> Path:
    """Idempotently ensure a supervisor is running. Returns socket path.

    Raises RuntimeError if a healthy supervisor cannot be reached within
    ``timeout`` seconds of spawning.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = _paths(data_dir)

    pong = _ping_socket(paths["socket"])
    if pong and pong.get("version") == PROTOCOL_VERSION:
        log.info("PTY supervisor already running (pid=%s)",
                 pong.get("pid"))
        return paths["socket"]

    old_pid = _read_pid_file(paths["pid"])
    if old_pid and _pid_alive(old_pid):
        log.info("Existing supervisor pid=%s does not respond — terminating",
                 old_pid)
        with contextlib.suppress(ProcessLookupError):
            os.kill(old_pid, signal.SIGTERM)
        # Reap if the dead supervisor was our child.
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(old_pid, os.WNOHANG)
    _cleanup_stale(paths)

    child_pid = spawn_detached(data_dir)
    log.info("Spawned PTY supervisor pid=%s", child_pid)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pong = _ping_socket(paths["socket"])
        if pong and pong.get("version") == PROTOCOL_VERSION:
            return paths["socket"]
        time.sleep(0.05)

    raise RuntimeError(
        f"PTY supervisor did not respond within {timeout:.1f}s "
        f"(log: {paths['log']})"
    )


# -- Entry point for ``python -m torque.pty_supervisor`` ---------------------


async def _exec_restart(
    server: asyncio.AbstractServer,
    supervisor: PtySupervisor,
    paths: dict,
    data_dir: Path,
    state_path: Path,
    restart_epoch: int,
    restart_nonce: str,
    expected_sessions: int,
) -> None:
    """Stop serving and replace this supervisor image in-place."""
    try:
        log.info(
            "PTY supervisor re-exec starting epoch=%s nonce=%s sessions=%s",
            restart_epoch,
            restart_nonce,
            expected_sessions,
        )
        server.close()
        await supervisor.detach_for_exec()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(server.wait_closed(), timeout=1.0)
        with contextlib.suppress(FileNotFoundError):
            paths["socket"].unlink()
        argv = [
            sys.executable,
            "-m",
            "torque.pty_supervisor",
            "--data-dir",
            str(data_dir),
            "--adopt-state",
            str(state_path),
            "--restart-epoch",
            str(int(restart_epoch or 0)),
            "--restart-nonce",
            str(restart_nonce or ""),
            "--expected-sessions",
            str(int(expected_sessions or 0)),
        ]
        os.execv(sys.executable, argv)
    except BaseException:
        log.exception("PTY supervisor re-exec failed; exiting for watchdog")
        os._exit(70)


async def _serve(
    data_dir: Path,
    *,
    adopt_state: Optional[Path] = None,
    restart_epoch: int = 0,
    restart_nonce: str = "",
    expected_sessions: int = 0,
) -> None:
    paths = _paths(data_dir)
    supervisor: PtySupervisor | None = None

    async def restart_callback(
        state_path: Path,
        epoch: int,
        nonce: str,
        session_count: int,
    ) -> None:
        assert supervisor is not None
        await _exec_restart(
            server,
            supervisor,
            paths,
            data_dir,
            state_path,
            epoch,
            nonce,
            session_count,
        )

    supervisor = PtySupervisor(
        data_dir=data_dir,
        restart_callback=restart_callback,
        restart_epoch=restart_epoch,
        restart_nonce=restart_nonce,
    )

    if adopt_state is not None:
        await supervisor.adopt_from_state(
            adopt_state,
            restart_epoch=restart_epoch,
            restart_nonce=restart_nonce,
            expected_sessions=expected_sessions,
        )

    async def handler(reader, writer):
        assert supervisor is not None
        await supervisor.handle_client(reader, writer)

    with contextlib.suppress(FileNotFoundError):
        paths["socket"].unlink()
    server = await asyncio.start_unix_server(handler, path=str(paths["socket"]))
    _set_server_sockets_cloexec(server)
    os.chmod(paths["socket"], 0o600)
    paths["pid"].write_text(str(os.getpid()), encoding="utf-8")
    os.chmod(paths["pid"], 0o600)

    def _signal_shutdown(*_args):
        asyncio.create_task(_graceful(server, supervisor, paths))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_shutdown)

    log.info(
        "PTY supervisor listening on %s (pid=%s restart_epoch=%s)",
        paths["socket"],
        os.getpid(),
        supervisor.restart_epoch,
    )
    async with server:
        try:
            await server.serve_forever()
        except asyncio.CancelledError:
            pass


async def _graceful(server, supervisor: PtySupervisor, paths: dict) -> None:
    log.info("PTY supervisor shutting down")
    server.close()
    await supervisor.shutdown()
    with contextlib.suppress(FileNotFoundError):
        paths["socket"].unlink()
    with contextlib.suppress(FileNotFoundError):
        paths["pid"].unlink()


def _configure_logging(log_path: Optional[Path]) -> None:
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = []
    if log_path:
        handlers.append(logging.FileHandler(str(log_path), encoding="utf-8"))
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--adopt-state", type=Path, default=None)
    parser.add_argument("--restart-epoch", type=int, default=0)
    parser.add_argument("--restart-nonce", default="")
    parser.add_argument("--expected-sessions", type=int, default=0)
    args = parser.parse_args(argv)
    _configure_logging(None)  # stdout/stderr already redirected by spawn
    try:
        asyncio.run(_serve(
            args.data_dir,
            adopt_state=args.adopt_state,
            restart_epoch=args.restart_epoch,
            restart_nonce=args.restart_nonce,
            expected_sessions=args.expected_sessions,
        ))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
