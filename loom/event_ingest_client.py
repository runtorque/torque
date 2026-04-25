"""Async daemon-side client for the event-ingest sidecar."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

from . import event_ingest_daemon, profiling
from .event_ingest_daemon import (
    PROTOCOL_VERSION,
    read_frame,
    write_frame,
)

log = logging.getLogger("loom.event_ingest_client")

_RESPONSE_TYPES = {"ok", "error", "pong", "drain", "status", "query"}


def _record_trimmed_ack_counter(response: dict) -> None:
    with contextlib.suppress(AttributeError, TypeError, ValueError):
        trimmed = int(response.get("trimmed") or 0)
        if trimmed > 0:
            profiling.recorder().incr("events_ingest_ring_trimmed", trimmed)


class EventIngestProtocolError(RuntimeError):
    pass


class EventIngestUnavailable(RuntimeError):
    pass


class EventIngestClient:
    """Persistent unix-socket client with reconnect + append retry."""

    def __init__(
        self,
        socket_path: Path | None = None,
        *,
        data_dir: Path | None = None,
        connect_timeout: float = 2.0,
        reconnect_delay: float = 0.25,
        ensure_running_func: Optional[Callable[..., Path]] = None,
    ):
        self.data_dir = Path(data_dir) if data_dir is not None else None
        if socket_path is None:
            if self.data_dir is None:
                raise ValueError("socket_path or data_dir is required")
            socket_path = self.data_dir / event_ingest_daemon.DEFAULT_SOCKET_NAME
        self.socket_path = Path(socket_path)
        self._connect_timeout = connect_timeout
        self._reconnect_delay = reconnect_delay
        self._ensure_running_func = ensure_running_func or event_ingest_daemon.ensure_running
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._request_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._pending: Optional[asyncio.Future] = None
        self._closed = False
        self._ready = asyncio.Event()
        self._reconnect_task: Optional[asyncio.Task] = None
        self.on_reconnect: Optional[
            Callable[[dict], Optional[Awaitable[None]]]
        ] = None
        self._last_daemon_pid: Optional[int] = None
        self._connection_generation = 0

    async def connect(self) -> dict:
        """Open the socket, verify protocol version, return pong payload."""
        self._closed = False
        async with self._request_lock:
            pong = await self._connect_once()
            self._last_daemon_pid = pong.get("pid")
            return pong

    async def aclose(self) -> None:
        self._closed = True
        self._ready.clear()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reconnect_task
        self._reconnect_task = None
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
        self._reader_task = None
        await self._close_transport(cancel_reader=True)
        if self._pending and not self._pending.done():
            self._pending.set_exception(EventIngestUnavailable("client closed"))
        self._pending = None

    async def append(
        self,
        event: dict,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        """Durably append one event envelope, retrying once after reconnect."""
        key = str(idempotency_key or uuid.uuid4().hex)
        return await self._call_with_reconnect_retry(
            "append",
            event=event,
            idempotency_key=key,
        )

    async def drain(self, *, since: int = 0, limit: int = 100) -> dict:
        return await self._call_with_reconnect_retry(
            "drain",
            since=int(since or 0),
            limit=int(limit or 100),
        )

    async def ack(self, *, up_to: int) -> dict:
        response = await self._call_with_reconnect_retry(
            "ack",
            up_to=int(up_to or 0),
        )
        _record_trimmed_ack_counter(response)
        return response

    async def status(self) -> dict:
        return await self._call_with_reconnect_retry("status")

    async def query(
        self,
        *,
        cell_id: str | None = None,
        cell_ids: list[str] | tuple[str, ...] | None = None,
        tool_name_pattern: str | None = None,
        hook_event_name: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 50,
    ) -> dict:
        payload = {
            "cell_id": str(cell_id or "").strip(),
            "cell_ids": list(cell_ids or []),
            "tool_name_pattern": str(tool_name_pattern or "").strip(),
            "hook_event_name": str(hook_event_name or "").strip(),
            "limit": int(limit or 50),
        }
        if since is not None:
            payload["since"] = float(since)
        if until is not None:
            payload["until"] = float(until)
        return await self._call_with_reconnect_retry("query", **payload)

    async def configure(
        self,
        *,
        max_rows: int | None = None,
        max_age_days: int | float | None = None,
        args_capture: str | None = None,
        full_capture_tools: list[str] | tuple[str, ...] | None = None,
    ) -> dict:
        payload = {}
        if max_rows is not None:
            payload["max_rows"] = int(max_rows)
        if max_age_days is not None:
            payload["max_age_days"] = float(max_age_days)
        if args_capture is not None:
            payload["args_capture"] = str(args_capture or "")
        if full_capture_tools is not None:
            payload["full_capture_tools"] = list(full_capture_tools or [])
        return await self._call_with_reconnect_retry("configure", **payload)

    async def call(self, op: str, **payload) -> dict:
        """Serialize one request and return its response frame."""
        if self._closed:
            raise EventIngestUnavailable("client closed")
        async with self._request_lock:
            await self._ensure_ready(connect_if_needed=True)
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending = fut
            try:
                await write_frame(self._writer, {"op": op, **payload})
            except Exception as exc:
                self._pending = None
                self._handle_disconnect()
                raise EventIngestUnavailable(str(exc)) from exc
            try:
                return await fut
            finally:
                if self._pending is fut:
                    self._pending = None

    async def _connect_once(self) -> dict:
        """Open one verified connection while request_lock is held.

        ``_pending`` is a single shared response future, so connect-time ping
        must be serialized with normal request/response calls.  Both
        foreground calls and the background reconnect loop route through
        ``_request_lock`` before entering here.
        """
        async with self._connect_lock:
            if self.data_dir is not None:
                self.socket_path = await asyncio.to_thread(
                    self._ensure_running_func,
                    self.data_dir,
                )
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path)),
                timeout=self._connect_timeout,
            )
            await self._close_transport(cancel_reader=True)
            self._connection_generation += 1
            generation = self._connection_generation
            self._reader = reader
            self._writer = writer
            self._ready.set()
            self._reader_task = asyncio.create_task(
                self._read_loop(reader, generation))
            try:
                pong = await self._probe_ping()
                if pong.get("type") != "pong":
                    raise EventIngestProtocolError(
                        f"expected pong, got {pong!r}")
                if pong.get("version") != PROTOCOL_VERSION:
                    raise EventIngestProtocolError(
                        f"version mismatch: client={PROTOCOL_VERSION} "
                        f"daemon={pong.get('version')!r}"
                    )
            except Exception:
                await self._close_transport(cancel_reader=True)
                raise
            return pong

    async def _probe_ping(self) -> dict:
        """Send the connect-time ping without re-entering request_lock."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending = fut
        try:
            await write_frame(self._writer, {"op": "ping"})
            return await fut
        finally:
            if self._pending is fut:
                self._pending = None

    async def _call_with_reconnect_retry(self, op: str, **payload) -> dict:
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                return await self.call(op, **payload)
            except EventIngestUnavailable as exc:
                last_exc = exc
                if attempt >= 1:
                    break
                await self._reconnect_now()
        raise EventIngestUnavailable(str(last_exc or "event ingest unavailable"))

    async def _ensure_ready(self, *, connect_if_needed: bool = True) -> None:
        if self._writer is None or self._reader is None:
            if connect_if_needed:
                await self._connect_once()
            else:
                raise EventIngestUnavailable("not connected")
        if not self._ready.is_set():
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=5.0)
            except asyncio.TimeoutError as exc:
                raise EventIngestUnavailable("event-ingest daemon not ready") from exc

    async def _read_loop(
        self,
        reader: asyncio.StreamReader,
        generation: int,
    ) -> None:
        try:
            while True:
                if generation != self._connection_generation:
                    break
                try:
                    msg = await read_frame(reader)
                except (ConnectionResetError, BrokenPipeError, OSError):
                    break
                except ValueError:
                    log.exception("Malformed frame from event-ingest daemon")
                    break
                if msg is None:
                    break
                await self._dispatch_frame(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Event-ingest client read loop failed")
        finally:
            if generation == self._connection_generation:
                self._handle_disconnect(generation=generation)

    async def _dispatch_frame(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype in _RESPONSE_TYPES:
            if self._pending and not self._pending.done():
                self._pending.set_result(msg)
                self._pending = None
            else:
                log.warning("Orphan response frame from event-ingest daemon: %s", mtype)
            return
        log.warning("Unknown frame type from event-ingest daemon: %s", mtype)

    def _handle_disconnect(self, *, generation: int | None = None) -> None:
        if generation is not None and generation != self._connection_generation:
            return
        self._ready.clear()
        if self._pending and not self._pending.done():
            self._pending.set_exception(EventIngestUnavailable("connection lost"))
            self._pending = None
        if self._writer:
            with contextlib.suppress(Exception):
                self._writer.close()
        self._reader = None
        self._writer = None
        if generation is None or generation == self._connection_generation:
            self._reader_task = None
        if self._closed:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._reconnect_delay)
            if (
                self._ready.is_set()
                and self._reader is not None
                and self._writer is not None
            ):
                return
            try:
                await self._reconnect_now()
            except Exception as exc:
                log.warning("Event-ingest reconnect failed: %s", exc)
                continue
            return

    async def _reconnect_now(self) -> dict:
        async with self._request_lock:
            prev_pid = self._last_daemon_pid
            pong = await self._connect_once()
            new_pid = pong.get("pid")
            self._last_daemon_pid = new_pid
        if self.on_reconnect:
            await _maybe_await(self.on_reconnect({
                "previous_pid": prev_pid,
                "supervisor_pid": new_pid,
                "daemon_pid": new_pid,
                "fresh": new_pid != prev_pid,
            }))
        return pong

    async def _close_transport(self, *, cancel_reader: bool = False) -> None:
        self._ready.clear()
        self._connection_generation += 1
        reader_task = self._reader_task
        self._reader_task = None
        if self._writer:
            with contextlib.suppress(Exception):
                self._writer.close()
                await self._writer.wait_closed()
        self._reader = None
        self._writer = None
        if (
            cancel_reader
            and reader_task
            and reader_task is not asyncio.current_task()
            and not reader_task.done()
        ):
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader_task


async def _maybe_await(result) -> None:
    if asyncio.iscoroutine(result):
        await result
