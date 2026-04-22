"""Standalone event-ingest sidecar.

The sidecar mirrors ``loom.pty_supervisor``'s lifecycle: a detached process
owned by Loom's data directory, a 0600 unix-domain socket, a pid file, and a
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

from .event_ingest_db import DEFAULT_MAX_ROWS, EventIngestStore

PROTOCOL_VERSION = 1
DEFAULT_SOCKET_NAME = "event_ingest.sock"
DEFAULT_PID_FILE_NAME = "event_ingest.pid"
DEFAULT_LOG_FILE_NAME = "event_ingest.log"
DEFAULT_DB_FILE_NAME = "event_ingest.db"
MAX_FRAME_BYTES = int(os.environ.get(
    "LOOM_EVENT_INGEST_MAX_FRAME_BYTES",
    str(4 * 1024 * 1024),
))

log = logging.getLogger("loom.event_ingest_daemon")


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
            "loom.event_ingest_daemon",
            "--data-dir",
            str(data_dir),
        ]
        if max_rows is not None:
            argv.extend(["--max-rows", str(max_rows)])
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
) -> Path:
    """Idempotently ensure event-ingest daemon is running."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = _paths(data_dir)

    pong = _ping_socket(paths["socket"])
    if pong and pong.get("version") == PROTOCOL_VERSION:
        log.info("Event-ingest daemon already running (pid=%s)", pong.get("pid"))
        return paths["socket"]

    old_pid = _read_pid_file(paths["pid"])
    if old_pid and _pid_alive(old_pid):
        log.info(
            "Existing event-ingest pid=%s does not respond — terminating",
            old_pid,
        )
        with contextlib.suppress(ProcessLookupError):
            os.kill(old_pid, signal.SIGTERM)
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(old_pid, os.WNOHANG)
    _cleanup_stale(paths)

    child_pid = spawn_detached(data_dir, max_rows=max_rows)
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


# -- Entry point for ``python -m loom.event_ingest_daemon`` ----------------


async def _serve(data_dir: Path, *, max_rows: int = DEFAULT_MAX_ROWS) -> None:
    paths = _paths(data_dir)
    store = EventIngestStore(paths["db"], max_rows=max_rows).init()
    daemon = EventIngestDaemon(store)

    async def handler(reader, writer):
        await daemon.handle_client(reader, writer)

    with contextlib.suppress(FileNotFoundError):
        paths["socket"].unlink()
    server = await asyncio.start_unix_server(handler, path=str(paths["socket"]))
    os.chmod(paths["socket"], 0o600)
    paths["pid"].write_text(str(os.getpid()), encoding="utf-8")
    os.chmod(paths["pid"], 0o600)

    def _signal_shutdown(*_args):
        asyncio.create_task(_graceful(server, daemon, paths))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_shutdown)

    log.info("Event-ingest daemon listening on %s (pid=%s)", paths["socket"], os.getpid())
    async with server:
        try:
            await server.serve_forever()
        except asyncio.CancelledError:
            pass


async def _graceful(server, daemon: EventIngestDaemon, paths: dict) -> None:
    log.info("Event-ingest daemon shutting down")
    server.close()
    await daemon.shutdown()
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
    parser.add_argument(
        "--max-rows",
        type=int,
        default=int(os.environ.get("LOOM_EVENT_INGEST_MAX_ROWS", DEFAULT_MAX_ROWS)),
    )
    args = parser.parse_args(argv)
    _configure_logging(None)  # stdout/stderr already redirected by spawn
    try:
        asyncio.run(_serve(args.data_dir, max_rows=args.max_rows))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
