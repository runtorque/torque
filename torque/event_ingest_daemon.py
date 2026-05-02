"""Standalone event-ingest sidecar.

The sidecar mirrors ``torque.pty_supervisor``'s lifecycle: a detached process
owned by Torque's data directory, a 0600 unix-domain socket, a pid file, and a
length-prefixed JSON protocol.  Its only responsibility is to durably append
agent-hook events to a local SQLite ring and hand them back to the main daemon
for asynchronous draining into ``EventBus``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .event_ingest_db import (
    DEFAULT_ARGS_CAPTURE,
    DEFAULT_MAX_AGE_DAYS,
    DEFAULT_MAX_ROWS,
    EventIngestStore,
)

PROTOCOL_VERSION = 2
DEFAULT_SOCKET_NAME = "event_ingest.sock"
DEFAULT_PID_FILE_NAME = "event_ingest.pid"
DEFAULT_LOG_FILE_NAME = "event_ingest.log"
DEFAULT_DB_FILE_NAME = "event_ingest.db"
MAX_FRAME_BYTES = int(os.environ.get(
    "TORQUE_EVENT_INGEST_MAX_FRAME_BYTES",
    str(4 * 1024 * 1024),
))

log = logging.getLogger("torque.event_ingest_daemon")


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


# -- Daemon protocol -------------------------------------------------------


class EventIngestDaemon:
    """In-process daemon. Tests use this directly behind a unix server."""

    def __init__(self, store: EventIngestStore):
        self.store = store

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername") or writer.get_extra_info("sockname")
        log.info("Event-ingest client connected: %s", peer)
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
                    await self._dispatch(msg, writer)
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
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
            log.info("Event-ingest client disconnected: %s", peer)

    async def _dispatch(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
    ) -> None:
        op = msg.get("op", "")
        if op == "ping":
            await write_frame(writer, {
                "type": "pong",
                "version": PROTOCOL_VERSION,
                "pid": os.getpid(),
            })
        elif op == "append":
            await self._op_append(msg, writer)
        elif op == "drain":
            await self._op_drain(msg, writer)
        elif op == "ack":
            await self._op_ack(msg, writer)
        elif op == "query":
            await self._op_query(msg, writer)
        elif op == "configure":
            await self._op_configure(msg, writer)
        elif op == "status":
            status = await asyncio.to_thread(self.store.status)
            await write_frame(writer, {"type": "status", **status})
        else:
            await write_frame(writer, {
                "type": "error",
                "code": "protocol_error",
                "message": f"unknown op: {op!r}",
            })

    async def _op_append(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
    ) -> None:
        key = str(msg.get("idempotency_key") or "").strip()
        event = msg.get("event")
        if not key or not isinstance(event, dict):
            await write_frame(writer, {
                "type": "error",
                "code": "protocol_error",
                "message": "append requires event object and idempotency_key",
            })
            return
        result = await asyncio.to_thread(self.store.append, event, key)
        await write_frame(writer, {
            "type": "ok",
            "op": "append",
            **result,
        })

    async def _op_drain(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
    ) -> None:
        since = int(msg.get("since") or 0)
        limit = int(msg.get("limit") or 100)
        result = await asyncio.to_thread(
            self.store.drain,
            since=since,
            limit=limit,
        )
        await write_frame(writer, {"type": "drain", **result})

    async def _op_ack(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
    ) -> None:
        up_to = int(msg.get("up_to") or 0)
        result = await asyncio.to_thread(self.store.ack, up_to=up_to)
        await write_frame(writer, {"type": "ok", "op": "ack", **result})

    async def _op_query(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            since = msg.get("since")
            since = float(since) if since is not None and since != "" else None
        except (TypeError, ValueError):
            since = None
        try:
            until = msg.get("until")
            until = float(until) if until is not None and until != "" else None
        except (TypeError, ValueError):
            until = None
        try:
            limit = int(msg.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        result = await asyncio.to_thread(
            self.store.query,
            cell_id=str(msg.get("cell_id") or "").strip() or None,
            cell_ids=list(msg.get("cell_ids") or []),
            tool_name_pattern=str(msg.get("tool_name_pattern") or "").strip() or None,
            hook_event_name=str(msg.get("hook_event_name") or "").strip() or None,
            since=since,
            until=until,
            limit=limit,
        )
        await write_frame(writer, {"type": "query", "events": result})

    async def _op_configure(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
    ) -> None:
        config = await asyncio.to_thread(
            self.store.configure,
            max_rows=msg.get("max_rows"),
            max_age_days=msg.get("max_age_days"),
            args_capture=msg.get("args_capture"),
            full_capture_tools=list(msg.get("full_capture_tools") or []),
        )
        await write_frame(writer, {"type": "ok", "op": "configure", **config})

    async def shutdown(self) -> None:
        await asyncio.to_thread(self.store.close)


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
        "db": data_dir / DEFAULT_DB_FILE_NAME,
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


def _terminate_pid(pid: int, *, timeout: float = 1.0) -> None:
    """Best-effort terminate for a stale sidecar pid."""
    if not _pid_alive(pid):
        return
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, os.WNOHANG)
        time.sleep(0.05)
    if _pid_alive(pid):
        log.warning("Event-ingest pid=%s did not exit after SIGTERM; killing", pid)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, os.WNOHANG)


def spawn_detached(data_dir: Path, *, max_rows: int | None = None) -> int:
    """Launch a detached ingest process. Returns the child PID."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = _paths(data_dir)
    log_fh = open(paths["log"], "ab", buffering=0)
    try:
        env = os.environ.copy()
        pkg_root = Path(__file__).resolve().parent.parent
        existing = env.get("PYTHONPATH", "")
        if str(pkg_root) not in existing.split(os.pathsep):
            env["PYTHONPATH"] = (
                str(pkg_root) + (os.pathsep + existing if existing else "")
            )
        argv = [
            sys.executable,
            "-m",
            "torque.event_ingest_daemon",
            "--data-dir",
            str(data_dir),
        ]
        if max_rows is not None:
            argv.extend(["--max-rows", str(max_rows)])
        max_age_days = env.get("TORQUE_EVENT_INGEST_MAX_AGE_DAYS", "")
        if max_age_days:
            argv.extend(["--max-age-days", str(max_age_days)])
        args_capture = env.get("TORQUE_MCP_CALL_LOG_ARGS_CAPTURE", "")
        if args_capture:
            argv.extend(["--args-capture", str(args_capture)])
        full_capture_tools = env.get("TORQUE_MCP_CALL_LOG_FULL_CAPTURE_TOOLS", "")
        if full_capture_tools:
            argv.extend(["--full-capture-tools", str(full_capture_tools)])
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


def ensure_running(
    data_dir: Path,
    *,
    timeout: float = 3.0,
    max_rows: int | None = None,
    max_age_days: int | float | None = None,
    args_capture: str | None = None,
    full_capture_tools: list[str] | tuple[str, ...] | None = None,
) -> Path:
    """Idempotently ensure event-ingest daemon is running."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = _paths(data_dir)

    pong = _ping_socket(paths["socket"])
    if pong and pong.get("version") == PROTOCOL_VERSION:
        log.info("Event-ingest daemon already running (pid=%s)", pong.get("pid"))
        return paths["socket"]
    if pong:
        old_version = pong.get("version")
        pong_pid = None
        try:
            pong_pid = int(pong.get("pid") or 0)
        except (TypeError, ValueError):
            pong_pid = None
        if pong_pid and _pid_alive(pong_pid):
            log.info(
                "Existing event-ingest pid=%s uses protocol version %r "
                "(wanted %s) — terminating",
                pong_pid,
                old_version,
                PROTOCOL_VERSION,
            )
            _terminate_pid(pong_pid)

    old_pid = _read_pid_file(paths["pid"])
    if old_pid and _pid_alive(old_pid):
        log.info(
            "Existing event-ingest pid=%s does not respond — terminating",
            old_pid,
        )
        _terminate_pid(old_pid)
    _cleanup_stale(paths)

    env_updates = {}
    if max_age_days is not None:
        env_updates["TORQUE_EVENT_INGEST_MAX_AGE_DAYS"] = str(max_age_days)
    if args_capture is not None:
        env_updates["TORQUE_MCP_CALL_LOG_ARGS_CAPTURE"] = str(args_capture)
    if full_capture_tools is not None:
        env_updates["TORQUE_MCP_CALL_LOG_FULL_CAPTURE_TOOLS"] = "\n".join(
            str(item or "").strip()
            for item in list(full_capture_tools or [])
            if str(item or "").strip()
        )
    old_env = {key: os.environ.get(key) for key in env_updates}
    try:
        os.environ.update(env_updates)
        child_pid = spawn_detached(data_dir, max_rows=max_rows)
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    log.info("Spawned event-ingest daemon pid=%s", child_pid)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pong = _ping_socket(paths["socket"])
        if pong and pong.get("version") == PROTOCOL_VERSION:
            return paths["socket"]
        time.sleep(0.05)

    raise RuntimeError(
        f"Event-ingest daemon did not respond within {timeout:.1f}s "
        f"(log: {paths['log']})"
    )


# -- Entry point for ``python -m torque.event_ingest_daemon`` ----------------


async def _serve(
    data_dir: Path,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_age_days: int | float = DEFAULT_MAX_AGE_DAYS,
    args_capture: str = DEFAULT_ARGS_CAPTURE,
    full_capture_tools: list[str] | None = None,
) -> None:
    paths = _paths(data_dir)
    store = EventIngestStore(
        paths["db"],
        max_rows=max_rows,
        max_age_days=max_age_days,
        args_capture=args_capture,
        full_capture_tools=full_capture_tools or [],
    ).init()
    daemon = EventIngestDaemon(store)
    stop_event = asyncio.Event()
    shutdown_task: asyncio.Task | None = None

    async def handler(reader, writer):
        await daemon.handle_client(reader, writer)

    with contextlib.suppress(FileNotFoundError):
        paths["socket"].unlink()
    server = await asyncio.start_unix_server(handler, path=str(paths["socket"]))
    os.chmod(paths["socket"], 0o600)
    paths["pid"].write_text(str(os.getpid()), encoding="utf-8")
    os.chmod(paths["pid"], 0o600)

    def _signal_shutdown(*_args):
        nonlocal shutdown_task
        if shutdown_task and not shutdown_task.done():
            return
        shutdown_task = asyncio.create_task(
            _graceful(server, daemon, paths, stop_event))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_shutdown)

    log.info("Event-ingest daemon listening on %s (pid=%s)", paths["socket"], os.getpid())
    async with server:
        await server.start_serving()
        await stop_event.wait()
    if shutdown_task:
        with contextlib.suppress(asyncio.CancelledError):
            await shutdown_task


async def _graceful(
    server,
    daemon: EventIngestDaemon,
    paths: dict,
    stop_event: asyncio.Event,
) -> None:
    log.info("Event-ingest daemon shutting down")
    server.close()
    with contextlib.suppress(Exception):
        await server.wait_closed()
    await daemon.shutdown()
    with contextlib.suppress(FileNotFoundError):
        paths["socket"].unlink()
    with contextlib.suppress(FileNotFoundError):
        paths["pid"].unlink()
    stop_event.set()


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
    parser.add_argument(
        "--max-rows",
        type=int,
        default=int(os.environ.get("TORQUE_EVENT_INGEST_MAX_ROWS", DEFAULT_MAX_ROWS)),
    )
    parser.add_argument(
        "--max-age-days",
        type=float,
        default=float(os.environ.get(
            "TORQUE_EVENT_INGEST_MAX_AGE_DAYS",
            DEFAULT_MAX_AGE_DAYS,
        )),
    )
    parser.add_argument(
        "--args-capture",
        default=os.environ.get(
            "TORQUE_MCP_CALL_LOG_ARGS_CAPTURE",
            DEFAULT_ARGS_CAPTURE,
        ),
    )
    parser.add_argument(
        "--full-capture-tools",
        default=os.environ.get("TORQUE_MCP_CALL_LOG_FULL_CAPTURE_TOOLS", ""),
    )
    args = parser.parse_args(argv)
    _configure_logging(None)  # stdout/stderr already redirected by spawn
    try:
        full_capture_tools = [
            item.strip()
            for item in str(args.full_capture_tools or "").replace(",", "\n").splitlines()
            if item.strip()
        ]
        asyncio.run(_serve(
            args.data_dir,
            max_rows=args.max_rows,
            max_age_days=args.max_age_days,
            args_capture=args.args_capture,
            full_capture_tools=full_capture_tools,
        ))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
