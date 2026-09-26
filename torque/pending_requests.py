"""Coordinate overlapping, explicitly keyed command requests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

class CommandRequestConflict(ValueError):
    pass


class PendingCommandWrites:
    """Complement persisted receipts for explicitly selected command writes.

    A disconnected waiter does not cancel its write. A matching retry joins
    the same operation until its result reaches the existing SQLite cache.
    This is not an execution guarantee across a daemon crash.
    """

    def __init__(self):
        self._pending: dict[str, tuple[str, asyncio.Task[Any]]] = {}

    async def run(self, key: str, request_hash: str,
                  operation: Callable[[], Awaitable[Any]]) -> Any:
        existing = self._pending.get(key)
        if existing:
            fingerprint, task = existing
            if fingerprint != request_hash:
                raise CommandRequestConflict(
                    "idempotency key was reused for a different command request")
        else:
            task = asyncio.create_task(operation())
            self._pending[key] = (request_hash, task)

            def completed(done):
                if self._pending.get(key, (None, None))[1] is done:
                    del self._pending[key]
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(completed)
        return await asyncio.shield(task)
