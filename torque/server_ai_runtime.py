"""AI runtime construction owned by the server composition root.

The server selects its database, state, and broadcast callback; this module
wires the optional embedding index and boot-summary services into that runtime.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .ai_embeddings import LocalEmbeddingService
from .ai_index import AIIndexService
from .ai_summaries import AISummaryService
from .config import log


@dataclass(frozen=True)
class AIRuntime:
    """AI services attached to one initialized Torque state instance."""

    embedding_service: LocalEmbeddingService
    index_service: AIIndexService
    summary_service: AISummaryService


def initialize_ai_runtime(
    *,
    db,
    state,
    data_dir: Path,
    broadcast_callback,
    loop: asyncio.AbstractEventLoop | None = None,
) -> AIRuntime:
    """Attach best-effort AI services and their delta observers to ``state``.

    Startup scheduling remains deliberately non-fatal: all direct state and
    database behavior continues if optional AI work cannot be queued.
    """
    embedding_service = LocalEmbeddingService(data_dir=data_dir)
    index_service = AIIndexService(
        db=db,
        state=state,
        embedding_service=embedding_service,
        data_dir=data_dir,
        broadcast_callback=broadcast_callback,
    )
    summary_service = AISummaryService(db=db, state=state)
    state.ai_embedding_service = embedding_service
    state.ai_index_service = index_service
    state.ai_summary_service = summary_service

    if bool(getattr(state.global_settings, "ai_enabled", False)):
        try:
            scheduler_loop = loop or asyncio.get_running_loop()
            scheduler_loop.call_later(
                3.0,
                index_service.schedule_incremental,
                "startup",
            )
            if bool(getattr(state.global_settings, "ai_boot_summary_enabled", True)):
                scheduler_loop.call_later(
                    3.0,
                    summary_service.schedule_all_boot_summaries,
                    "startup",
                )
        except Exception:
            log.exception("Failed to schedule startup AI jobs")

    summary_delta_ops = {
        "architect_journal_append",
        "journal_append",
        "decision_upsert",
        "decision_remove",
    }
    indexed_delta_ops = summary_delta_ops | {
        "task_upsert",
        "task_remove",
        "agent_peer_thread_upsert",
        "agent_peer_thread_remove",
    }

    def schedule_from_delta(delta: dict) -> None:
        op = str((delta or {}).get("op", "") or "")
        index_service.schedule_incremental(op)
        if op in summary_delta_ops:
            summary_service.schedule_for_delta(delta)

    state.register_delta_observer(schedule_from_delta, ops=indexed_delta_ops)
    return AIRuntime(
        embedding_service=embedding_service,
        index_service=index_service,
        summary_service=summary_service,
    )
