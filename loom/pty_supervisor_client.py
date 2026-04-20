"""Async client for the PTY supervisor.

Maintains a single persistent unix-domain-socket connection and
multiplexes synchronous request/response ops against asynchronous
streaming frames (snapshot / output / exit) per session.

Auto-reconnects on connection loss, then re-subscribes to every
session that had an output callback registered before the drop —
this is the path exercised during Loom daemon restart.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .pty_supervisor import (
    PROTOCOL_VERSION,
    read_frame,
    write_frame,
)

log = logging.getLogger("loom.pty_supervisor_client")

_RESPONSE_TYPES = {"ok", "error", "pong", "list"}

# Callbacks are (msg_dict) -> None | coroutine.
OutputCallback = Callable[[dict], Optional[Awaitable[None]]]
ExitCallback = Callable[[dict], Optional[Awaitable[None]]]


@dataclass
class _Subscription:
    session_id: str
    on_output: Optional[OutputCallback] = None
    on_exit: Optional[ExitCallback] = None


class SupervisorProtocolError(RuntimeError):
    pass


class SupervisorUnavailable(RuntimeError):
    pass


class PtySupervisorClient:
    """Daemon-side client.

    Open with ``await client.connect()``; make calls with
    ``await client.create(...)`` / ``write`` / ``resize`` / ``close`` /
    ``list`` / ``subscribe`` / ``unsubscribe``. Close with
    ``await client.aclose()``.
    """

    def __init__(
        self,
        socket_path: Path,
        *,
        connect_timeout: float = 2.0,
        reconnect_delay: float = 0.5,
    ):
        self.socket_path = Path(socket_path)
        self._connect_timeout = connect_timeout
        self._reconnect_delay = reconnect_delay
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._request_lock = asyncio.Lock()
        self._pending: Optional[asyncio.Future] = None
        self._subscriptions: dict[str, _Subscription] = {}
        self._closed = False
        self._ready = asyncio.Event()
        self._reconnect_task: Optional[asyncio.Task] = None
        # Fires after a reconnect with a fresh supervisor identity
        # (different pid than the previous one). Used by callers that
        # want to reconcile their session list via ``list``.
        self.on_reconnect: Optional[
            Callable[[dict], Optional[Awaitable[None]]]] = None
        self._last_supervisor_pid: Optional[int] = None

    # -- connect / close ---------------------------------------------------

    async def connect(self) -> dict:
        """Open the socket, verify version, return pong payload."""
        self._closed = False
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(self.socket_path)),
            timeout=self._connect_timeout,
        )
        self._reader = reader
        self._writer = writer
        self._ready.set()
        self._reader_task = asyncio.create_task(self._read_loop())
        pong = await self.call("ping")
        if pong.get("type") != "pong":
            raise SupervisorProtocolError(
                f"expected pong, got {pong!r}")
        if pong.get("version") != PROTOCOL_VERSION:
            raise SupervisorProtocolError(
                f"version mismatch: client={PROTOCOL_VERSION} "
                f"supervisor={pong.get('version')!r}")
        self._last_supervisor_pid = pong.get("pid")
        return pong

    async def aclose(self) -> None:
        self._closed = True
        self._ready.clear()
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
        self._reader_task = None
        if self._writer:
            with contextlib.suppress(Exception):
                self._writer.close()
                await self._writer.wait_closed()
        self._reader = None
        self._writer = None
        if self._pending and not self._pending.done():
            self._pending.set_exception(
                SupervisorUnavailable("client closed"))
        self._pending = None

    # -- high-level ops ----------------------------------------------------

    async def create(
        self,
        *,
        session_id: str,
        cell_id: str,
        shell_argv: list,
        env: dict,
        cwd: str,
        cols: int,
        rows: int,
        bootstrap_dir: str = "",
    ) -> dict:
        return await self.call(
            "create",
            session_id=session_id,
            cell_id=cell_id,
            shell_argv=list(shell_argv),
            env={str(k): str(v) for k, v in env.items()},
            cwd=cwd,
            cols=cols,
            rows=rows,
            bootstrap_dir=bootstrap_dir,
        )

    async def write_input(self, session_id: str, data: bytes) -> dict:
        return await self.call(
            "write",
            session_id=session_id,
            data=base64.b64encode(data).decode("ascii"),
        )

    async def resize(self, session_id: str, cols: int, rows: int) -> dict:
        return await self.call(
            "resize",
            session_id=session_id,
            cols=int(cols),
            rows=int(rows),
        )

    async def close_session(self, session_id: str) -> dict:
        return await self.call("close", session_id=session_id)

    async def list_sessions(self) -> list:
        result = await self.call("list")
        return list(result.get("sessions") or [])

    async def subscribe(
        self,
        session_id: str,
        *,
        on_output: Optional[OutputCallback] = None,
        on_exit: Optional[ExitCallback] = None,
    ) -> dict:
        self._subscriptions[session_id] = _Subscription(
            session_id=session_id,
            on_output=on_output,
            on_exit=on_exit,
        )
        return await self.call("subscribe", session_id=session_id)

    async def unsubscribe(self, session_id: str) -> dict:
        self._subscriptions.pop(session_id, None)
        return await self.call("unsubscribe", session_id=session_id)

    # -- core request/response --------------------------------------------

    async def call(self, op: str, **payload) -> dict:
        """Serialize a request, return its response frame."""
        if self._closed:
            raise SupervisorUnavailable("client closed")
        async with self._request_lock:
            await self._ensure_ready()
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending = fut
            try:
                await write_frame(self._writer, {"op": op, **payload})
            except Exception as exc:
                self._pending = None
                self._handle_disconnect()
                raise SupervisorUnavailable(str(exc)) from exc
            try:
                return await fut
            finally:
                if self._pending is fut:
                    self._pending = None

    async def _ensure_ready(self) -> None:
        if self._writer is None or self._reader is None:
            raise SupervisorUnavailable("not connected")
        if not self._ready.is_set():
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=5.0)
            except asyncio.TimeoutError as exc:
                raise SupervisorUnavailable(
                    "supervisor not ready") from exc

    # -- dispatcher --------------------------------------------------------

    async def _read_loop(self) -> None:
        try:
            while True:
                if self._reader is None:
                    break
                try:
                    msg = await read_frame(self._reader)
                except (ConnectionResetError, BrokenPipeError, OSError):
                    break
                except ValueError:
                    log.exception("Malformed frame from supervisor")
                    break
                if msg is None:
                    break
                await self._dispatch_frame(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Client read loop failed")
        finally:
            self._handle_disconnect()

    async def _dispatch_frame(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype in _RESPONSE_TYPES:
            if self._pending and not self._pending.done():
                self._pending.set_result(msg)
                self._pending = None
            else:
                log.warning(
                    "Orphan response frame: %s", mtype)
            return
        if mtype in ("snapshot", "output"):
            sub = self._subscriptions.get(msg.get("session_id", ""))
            if sub and sub.on_output:
                await _maybe_await(sub.on_output(msg))
            return
        if mtype == "exit":
            sub = self._subscriptions.get(msg.get("session_id", ""))
            if sub and sub.on_exit:
                await _maybe_await(sub.on_exit(msg))
            return
        log.warning("Unknown frame type from supervisor: %s", mtype)

    def _handle_disconnect(self) -> None:
        self._ready.clear()
        if self._pending and not self._pending.done():
            self._pending.set_exception(
                SupervisorUnavailable("connection lost"))
            self._pending = None
        if self._writer:
            with contextlib.suppress(Exception):
                self._writer.close()
        self._reader = None
        self._writer = None
        if self._closed:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._reconnect_delay)
            try:
                pong = await self.connect()
            except Exception as exc:
                log.warning("Supervisor reconnect failed: %s", exc)
                continue
            prev_pid = self._last_supervisor_pid
            new_pid = pong.get("pid")
            if new_pid != prev_pid:
                log.info(
                    "Supervisor pid changed: %s -> %s (fresh instance)",
                    prev_pid, new_pid)
            self._last_supervisor_pid = new_pid
            await self._resubscribe_all()
            if self.on_reconnect:
                await _maybe_await(self.on_reconnect({
                    "previous_pid": prev_pid,
                    "supervisor_pid": new_pid,
                    "fresh": new_pid != prev_pid,
                }))
            return

    async def _resubscribe_all(self) -> None:
        for sub in list(self._subscriptions.values()):
            try:
                await self.call("subscribe", session_id=sub.session_id)
            except Exception:
                log.exception("Resubscribe failed for %s", sub.session_id)


async def _maybe_await(result) -> None:
    if asyncio.iscoroutine(result):
        await result
