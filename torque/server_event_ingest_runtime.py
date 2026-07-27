"""Event-ingest client startup and reconnect wiring for the server runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .config import log
from .event_ingest_client import EventIngestClient
from .events import EventIngestDrainer


@dataclass(frozen=True)
class EventIngestRuntime:
    """Connected (or retryable) event-ingest components for one daemon."""

    client: EventIngestClient
    configured: list[bool]
    drainer: EventIngestDrainer
    ensure_configured: Callable[[], Awaitable[None]]


async def initialize_event_ingest_runtime(
    *,
    data_dir: Path,
    event_bus,
    state,
    daemon_identity: str,
    configure_client: Callable[..., Awaitable[None]],
    client_factory=EventIngestClient,
    drainer_factory=EventIngestDrainer,
) -> EventIngestRuntime:
    """Connect event ingestion when available and retain its retry contract."""
    client = client_factory(data_dir=data_dir)
    configured = [False]
    try:
        await client.connect()
        await configure_client(client, state)
        configured[0] = True
        log.info("Event ingest daemon connected at %s", client.socket_path)
    except Exception:
        # Keep startup alive; endpoint appends and the drainer both retry via
        # ensure_running on demand. If append still cannot persist an event,
        # /events returns 503 instead of pretending the event is safe.
        log.exception("Event ingest daemon unavailable at startup")

    drainer = drainer_factory(
        client,
        event_bus,
        state,
        daemon_identity=daemon_identity,
    )

    async def ensure_configured() -> None:
        if configured[0]:
            return
        await configure_client(client, state)
        configured[0] = True

    async def on_reconnect(_info) -> None:
        configured[0] = False
        await ensure_configured()

    client.on_reconnect = on_reconnect
    return EventIngestRuntime(
        client=client,
        configured=configured,
        drainer=drainer,
        ensure_configured=ensure_configured,
    )
