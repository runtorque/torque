"""Standalone-mode PTY supervisor sidecar.

This process owns every PTY master FD and its child subprocess in Loom's
standalone mode. The Loom daemon connects over a local unix-domain
socket and exchanges length-prefixed JSON frames to create, write to,
resize, subscribe to, list, and close sessions.

The supervisor is spawned with ``start_new_session=True`` and intended
to outlive the Loom daemon across ``os.execv`` restarts or daemon
crashes: if the daemon goes away, sessions keep running; when a new
daemon starts, it pings the existing socket and re-subscribes to
existing sessions.

Scope: STANDALONE mode only. The toolbelt/iTerm2 path does not use
this — ``ITerm2Adapter`` continues to own its sessions via the iTerm2
Python API.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import errno
import json
import logging
import os
import pty
import signal
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .pty_core import (
    BUFFER_LIMIT,
    Utf8IncrementalDecoder,
    preexec_acquire_ctty,
    set_winsize,
)

PROTOCOL_VERSION = 1
DEFAULT_SOCKET_NAME = "pty_supervisor.sock"
DEFAULT_PID_FILE_NAME = "pty_supervisor.pid"
DEFAULT_LOG_FILE_NAME = "pty_supervisor.log"

# Max JSON frame size to accept before rejecting as protocol error.
# Snapshot replay can be large (up to BUFFER_LIMIT bytes base64-encoded).
MAX_FRAME_BYTES = 2 * BUFFER_LIMIT + 4096

log = logging.getLogger("loom.pty_supervisor")


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
    buffer: bytearray = field(default_factory=bytearray)
    total_bytes: int = 0
    subscribers: set = field(default_factory=set)
    read_task: Optional[asyncio.Task] = None
    process: Optional[asyncio.subprocess.Process] = None
    closed: bool = False
    exit_status: Optional[int] = None

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
            "shell_argv": list(self.shell_argv),
            "cwd": self.cwd,
        }


# -- Supervisor ------------------------------------------------------------


class PtySupervisor:
    """In-process supervisor. Also used directly by tests."""

    def __init__(self):
        self.sessions: dict[str, SupervisorSession] = {}
        self._lock = asyncio.Lock()

    # -- client connections ------------------------------------------------

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        subs: set[str] = set()
        peer = writer.get_extra_info("peername") or writer.get_extra_info("sockname")
        log.info("Client connected: %s", peer)
        try:
            while True:
                try:
                    msg = await read_frame(reader)
                except ValueError as exc:
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
                    await _safe_write(writer, {
                        "type": "error",
                        "code": "internal_error",
                        "message": str(exc),
                    })
        finally:
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
        op = msg.get("op", "")
        if op == "ping":
            await write_frame(writer, {
                "type": "pong",
                "version": PROTOCOL_VERSION,
                "pid": os.getpid(),
            })
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
                "sessions": [s.snapshot() for s in self.sessions.values()],
            })
        elif op == "subscribe":
            await self._op_subscribe(msg, writer, subs)
        elif op == "unsubscribe":
            await self._op_unsubscribe(msg, writer, subs)
        else:
            await write_frame(writer, {
                "type": "error",
                "code": "protocol_error",
                "message": f"unknown op: {op!r}",
            })

    # -- ops ---------------------------------------------------------------

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
            await write_frame(writer, {
                "type": "error",
                "code": "protocol_error",
                "message": "create requires session_id and shell_argv",
            })
            return
        if session_id in self.sessions:
            await write_frame(writer, {
                "type": "error",
                "code": "session_exists",
                "message": session_id,
            })
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
            await write_frame(writer, {
                "type": "error",
                "code": "shell_spawn_failed",
                "message": str(exc),
            })
            return
        self.sessions[session_id] = session
        session.read_task = asyncio.create_task(self._read_loop(session))
        await write_frame(writer, {
            "type": "ok",
            "op": "create",
            "session_id": session_id,
            "pid": session.pid,
        })

    async def _op_write(self, msg: dict, writer: asyncio.StreamWriter) -> None:
        session_id = str(msg.get("session_id") or "")
        data_b64 = msg.get("data") or ""
        sess = self.sessions.get(session_id)
        if not sess or sess.closed:
            await write_frame(writer, {
                "type": "error",
                "code": "unknown_session",
                "message": session_id,
            })
            return
        try:
            payload = base64.b64decode(data_b64) if data_b64 else b""
        except Exception as exc:
            await write_frame(writer, {
                "type": "error",
                "code": "protocol_error",
                "message": f"bad base64: {exc}",
            })
            return
        if payload:
            try:
                await asyncio.to_thread(os.write, sess.master_fd, payload)
            except OSError as exc:
                await write_frame(writer, {
                    "type": "error",
                    "code": "write_failed",
                    "message": str(exc),
                })
                return
        await write_frame(writer, {"type": "ok", "op": "write"})

    async def _op_resize(self, msg: dict, writer: asyncio.StreamWriter) -> None:
        session_id = str(msg.get("session_id") or "")
        cols = max(1, int(msg.get("cols") or 0))
        rows = max(1, int(msg.get("rows") or 0))
        sess = self.sessions.get(session_id)
        if not sess or sess.closed:
            await write_frame(writer, {
                "type": "error",
                "code": "unknown_session",
                "message": session_id,
            })
            return
        sess.cols = cols
        sess.rows = rows
        set_winsize(sess.master_fd, cols, rows)
        await write_frame(writer, {"type": "ok", "op": "resize"})

    async def _op_close(self, msg: dict, writer: asyncio.StreamWriter) -> None:
        session_id = str(msg.get("session_id") or "")
        sess = self.sessions.get(session_id)
        if not sess:
            await write_frame(writer, {
                "type": "error",
                "code": "unknown_session",
                "message": session_id,
            })
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
            await write_frame(writer, {
                "type": "error",
                "code": "unknown_session",
                "message": session_id,
            })
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
            process=process,
        )

    async def _read_loop(self, session: SupervisorSession) -> None:
        try:
            while True:
                try:
                    chunk = await asyncio.to_thread(
                        os.read, session.master_fd, 4096)
                except OSError as exc:
                    if exc.errno in (errno.EIO, errno.EBADF):
                        break
                    raise
                if not chunk:
                    break
                session.total_bytes += len(chunk)
                session.buffer.extend(chunk)
                if len(session.buffer) > BUFFER_LIMIT:
                    del session.buffer[:len(session.buffer) - BUFFER_LIMIT]
                await self._broadcast(session, chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Read loop failed for %s", session.session_id)
        finally:
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


def _ping_socket(socket_path: Path, timeout: float = 1.0) -> Optional[dict]:
    """Blocking unix-socket ping. Returns pong payload on success."""
    if not socket_path.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
    except OSError:
        return None
    try:
        body = json.dumps({"op": "ping"}).encode("utf-8")
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
        # Ensure the child can import ``loom.*``. The daemon's entrypoint
        # inserts SCRIPT_DIR into sys.path; propagate it via PYTHONPATH
        # so ``python -m loom.pty_supervisor`` resolves.
        pkg_root = Path(__file__).resolve().parent.parent
        existing = env.get("PYTHONPATH", "")
        if str(pkg_root) not in existing.split(os.pathsep):
            env["PYTHONPATH"] = (
                str(pkg_root) + (os.pathsep + existing if existing else "")
            )
        argv = [
            sys.executable,
            "-m",
            "loom.pty_supervisor",
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


# -- Entry point for ``python -m loom.pty_supervisor`` ---------------------


async def _serve(data_dir: Path) -> None:
    paths = _paths(data_dir)
    supervisor = PtySupervisor()

    async def handler(reader, writer):
        await supervisor.handle_client(reader, writer)

    with contextlib.suppress(FileNotFoundError):
        paths["socket"].unlink()
    server = await asyncio.start_unix_server(handler, path=str(paths["socket"]))
    os.chmod(paths["socket"], 0o600)
    paths["pid"].write_text(str(os.getpid()), encoding="utf-8")
    os.chmod(paths["pid"], 0o600)

    def _signal_shutdown(*_args):
        asyncio.create_task(_graceful(server, supervisor, paths))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_shutdown)

    log.info("PTY supervisor listening on %s (pid=%s)",
             paths["socket"], os.getpid())
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
    args = parser.parse_args(argv)
    _configure_logging(None)  # stdout/stderr already redirected by spawn
    try:
        asyncio.run(_serve(args.data_dir))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
