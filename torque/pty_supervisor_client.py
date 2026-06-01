"""Async client for the PTY supervisor.

Maintains a single persistent unix-domain-socket connection and
multiplexes synchronous request/response ops against asynchronous
streaming frames (snapshot / output / exit) per session.

Auto-reconnects on connection loss, then re-subscribes to every
session that had an output callback registered before the drop —
this is the path exercised during Torque daemon restart.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .pty_supervisor import (
    PROTOCOL_VERSION,
    read_frame,
    write_frame,
)

log = logging.getLogger("torque.pty_supervisor_client")

_RESPONSE_TYPES = {"ok", "error", "pong", "list", "metrics"}

# Upper bound for a single request/response round-trip. Supervisor ops
# (list/write/create/resize/close) are sub-second in practice; this only
# exists so a lost or never-dispatched response cannot leave a request
# awaiting forever while holding ``_request_lock`` — which would wedge every
# later list/write on the shared client (the panel reads "Not loaded" and
# user→agent messages silently stall). On timeout we drop the connection so
# the reconnect loop rebuilds a clean channel instead of hanging permanently.
_CALL_TIMEOUT_SECONDS = 30.0

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
        self._last_supervisor_started_at: Optional[float] = None
        # Monotonic id for the live connection. Bumped on every connect and on
        # every disconnect so a stale ``_read_loop`` task (left over from a
        # prior connection) can't tear down the connection that replaced it —
        # which otherwise oscillates connect→disconnect forever.
        self._connection_gen = 0
        # Latency (ms) of the most recent successful request round-trip, for
        # the daemon↔supervisor health surface. None until the first call.
        self._last_op_latency_ms: Optional[float] = None
        self._last_successful_op_at: Optional[float] = None
        self._last_reconnect_at: Optional[float] = None
        self._reconnect_count: int = 0
        self._reconnect_failures: int = 0

    def is_connected(self) -> bool:
        return (
            not self._closed
            and self._ready.is_set()
            and self._writer is not None
            and self._reader is not None
        )

    @property
    def last_op_latency_ms(self) -> Optional[float]:
        return self._last_op_latency_ms

    @property
    def last_successful_op_at(self) -> Optional[float]:
        return self._last_successful_op_at

    @property
    def last_supervisor_pid(self) -> Optional[int]:
        return self._last_supervisor_pid

    @property
    def last_supervisor_started_at(self) -> Optional[float]:
        return self._last_supervisor_started_at

    @property
    def last_reconnect_at(self) -> Optional[float]:
        return self._last_reconnect_at

    @property
    def reconnect_count(self) -> int:
        return int(self._reconnect_count)

    @property
    def reconnect_failures(self) -> int:
        return int(self._reconnect_failures)

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
        self._connection_gen += 1
        gen = self._connection_gen
        self._reader_task = asyncio.create_task(self._read_loop(gen))
        pong = await self.call("ping")
        if pong.get("type") != "pong":
            raise SupervisorProtocolError(
                f"expected pong, got {pong!r}")
        if pong.get("version") != PROTOCOL_VERSION:
            raise SupervisorProtocolError(
                f"version mismatch: client={PROTOCOL_VERSION} "
                f"supervisor={pong.get('version')!r}")
        self._last_supervisor_pid = pong.get("pid")
        try:
            self._last_supervisor_started_at = float(
                pong.get("started_at") or 0.0) or None
        except (TypeError, ValueError):
            self._last_supervisor_started_at = None
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

    async def list_state(self) -> dict:
        """Return the raw list-state payload from the supervisor.

        ``list_sessions`` preserves the original list-only API for callers
        that only care about PTY rows. Newer diagnostics also need the
        top-level supervisor process metadata exposed by the same wire op.
        """
        result = await self.call("list")
        return {
            "type": result.get("type"),
            "supervisor": dict(result.get("supervisor") or {}),
            "sessions": list(result.get("sessions") or []),
        }

    async def list_sessions(self) -> list:
        result = await self.list_state()
        return list(result.get("sessions") or [])

    async def metrics(self) -> dict:
        result = await self.call("metrics")
        return dict(result.get("metrics") or {})

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
            started = loop.time()
            try:
                result = await asyncio.wait_for(
                    fut, timeout=_CALL_TIMEOUT_SECONDS)
                self._last_op_latency_ms = (loop.time() - started) * 1000.0
                if result.get("type") != "error":
                    self._last_successful_op_at = time.time()
                return result
            except asyncio.TimeoutError as exc:
                # Response was lost or never dispatched while the socket stayed
                # nominally open. Tear the connection down so _reconnect_loop
                # rebuilds a clean channel; without this the lock is held
                # forever and every later list/write wedges.
                self._pending = None
                self._handle_disconnect()
                raise SupervisorUnavailable(
                    f"supervisor op {op!r} timed out after "
                    f"{_CALL_TIMEOUT_SECONDS:.0f}s") from exc
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

    async def _read_loop(self, gen: int) -> None:
        try:
            while True:
                if self._reader is None or gen != self._connection_gen:
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
            self._handle_disconnect(gen)

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

    def _handle_disconnect(self, gen: Optional[int] = None) -> None:
        # Ignore teardown requests from a stale reader task (one whose
        # connection has already been replaced); only the current connection
        # may disconnect itself.
        if gen is not None and gen != self._connection_gen:
            return
        # Invalidate the current connection so the now-stale reader task's
        # eventual finally is a no-op against whatever connects next.
        self._connection_gen += 1
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
            prev_pid = self._last_supervisor_pid
            try:
                pong = await self.connect()
            except Exception as exc:
                self._reconnect_failures += 1
                log.warning("Supervisor reconnect failed: %s", exc)
                continue
            new_pid = pong.get("pid")
            if new_pid != prev_pid:
                log.info(
                    "Supervisor pid changed: %s -> %s (fresh instance)",
                    prev_pid, new_pid)
            self._last_supervisor_pid = new_pid
            self._reconnect_count += 1
            self._reconnect_failures = 0
            self._last_reconnect_at = time.time()
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
