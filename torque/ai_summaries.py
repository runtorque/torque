"""Cached AI summaries for Architect/Engineer boot recovery.

This module is deliberately best-effort.  Boot paths and MCP read tools only
return cached rows; summary generation is scheduled out-of-band and every
failure leaves raw journal/decision recovery paths available unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from torque import ai
from torque.ai import LLMClient, LLMFailure, LLMResult

log = logging.getLogger("torque")

PROMPT_VERSION = "boot-summary-v1"
ARCHITECT_BOOT_TYPE = "architect_boot"
ENGINEER_BOOT_TYPE = "engineer_boot"
SUMMARY_STATUSES = {"empty", "ready", "stale", "refreshing", "error"}

DEFAULT_DEBOUNCE_SECONDS = 3.0
DEFAULT_MIN_INTERVAL_SECONDS = 600
DEFAULT_MAX_REFRESHES_PER_HOUR = 20
REFRESH_CAP_WINDOW_SECONDS = 3600.0
RATE_LIMIT_INITIAL_BACKOFF_SECONDS = 60.0
RATE_LIMIT_MAX_BACKOFF_SECONDS = 3600.0
MAX_SOURCE_ROWS = 240
MAX_FULL_SOURCE_CHARS = 36_000
MAX_DELTA_SOURCE_CHARS = 8_000
MAX_DELTA_ROWS = 8
CHUNK_TARGET_CHARS = 14_000
MAX_CHUNKS = 4

SummarizeFunc = Callable[..., Awaitable[ai.LLMResponse]]


@dataclass(frozen=True)
class SummarySource:
    source_key: str
    source_type: str
    title: str
    text: str
    content_hash: str


@dataclass(frozen=True)
class SummarySourceBundle:
    summary_key: str
    summary_type: str
    scope_kind: str
    scope_ref: str
    provider: str
    model: str
    prompt_version: str
    source_hash: str
    source_counts: dict
    sources: list[SummarySource]


class AISummaryService:
    """Out-of-band boot-summary cache refresher."""

    def __init__(
        self,
        *,
        db,
        state=None,
        llm_client: LLMClient | None = None,
        summarize_func: SummarizeFunc | None = None,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        self.db = db
        self.state = state
        self.llm_client = llm_client
        self.summarize_func = summarize_func or ai.summarize
        self.debounce_seconds = max(0.05, float(debounce_seconds))
        self._tasks: dict[str, asyncio.Task] = {}
        self._read_tasks: dict[str, asyncio.Task] = {}
        self._refreshing: set[str] = set()
        self._last_attempt: dict[str, float] = {}
        self._hour_window_started_at = time.monotonic()
        self._hour_attempts = 0
        self._scope_cooldown_until: dict[str, float] = {}
        self._scope_backoff_seconds: dict[str, float] = {}
        self._global_cooldown_until = 0.0
        self._global_backoff_seconds = 0.0
        self._closed = False

    def cached_summary(self, summary_key: str) -> dict:
        """Return the persisted cache row or an empty synthetic row."""

        summary_key = str(summary_key or "").strip()
        row = self._load_summary(summary_key) if summary_key else None
        return _public_summary_row(row or _synthetic_summary_row(summary_key))

    def cached_summary_for_read(self, summary_key: str) -> dict:
        """Return a cached row and maybe schedule one lazy background refresh.

        This method is intentionally synchronous and never calls an LLM inline.
        When the row is stale (or empty with known source material), it schedules
        a gated out-of-band refresh for a later read.
        """

        summary_key = str(summary_key or "").strip()
        raw = self._load_summary(summary_key) if summary_key else None
        row = raw or _synthetic_summary_row(summary_key)
        status = str(row.get("status", "") or "empty").strip()
        if status == "refreshing" and not self._has_live_refresh_task(summary_key):
            row = self._downgrade_stranded_refreshing_row(row)
            status = str(row.get("status", "") or "empty").strip()
        if status == "empty" and not _row_has_sources(row):
            with contextlib.suppress(Exception):
                row = self.mark_stale_if_needed(summary_key)
                status = str(row.get("status", "") or "empty").strip()
        should_refresh = status == "stale" or (
            status == "empty" and _row_has_sources(row)
        )
        gate_message = ""
        if should_refresh:
            gate_message = self.schedule_read_refresh(summary_key)
        if gate_message:
            row = {**row, "error": gate_message}
        return _public_summary_row(row)

    def schedule_read_refresh(self, summary_key: str) -> str:
        """Schedule one read-triggered refresh if gates allow it.

        Returns a public-facing degradation message when a cap/cooldown prevents
        scheduling; otherwise returns an empty string.
        """

        summary_key = str(summary_key or "").strip()
        if not summary_key or self._closed:
            return ""
        if not self._boot_summary_generation_enabled():
            return ""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return ""
        now = time.monotonic()
        cooldown_message = self._cooldown_message(summary_key, now=now)
        if cooldown_message:
            self._record_gate_message(summary_key, cooldown_message)
            return cooldown_message
        if self._hourly_cap_exhausted(now=now):
            message = self._cap_message(now=now)
            self._record_gate_message(summary_key, message)
            return message
        min_interval = self._min_interval_seconds()
        last = float(self._last_attempt.get(summary_key, 0.0) or 0.0)
        if min_interval > 0 and last and now - last < min_interval:
            return ""
        if summary_key in self._refreshing:
            return ""
        prior = self._read_tasks.get(summary_key)
        if prior is not None and not prior.done():
            return ""
        if not self._consume_hourly_attempt(now=now):
            message = self._cap_message(now=now)
            self._record_gate_message(summary_key, message)
            return message

        # Stamp before task creation so concurrent reads cannot double-fire.
        self._last_attempt[summary_key] = now
        self._read_tasks[summary_key] = loop.create_task(
            self._read_refresh_task(summary_key)
        )
        return ""

    def _has_live_refresh_task(self, summary_key: str) -> bool:
        if summary_key in self._refreshing:
            return True
        task = self._read_tasks.get(summary_key)
        return bool(task is not None and not task.done())

    def schedule_for_delta(self, delta: dict) -> None:
        """Schedule a stale-check for a state mutation delta.

        Mutation paths never call the provider.  Reads are responsible for
        scheduling any out-of-band refresh once they observe stale cached rows.
        """

        if self._closed:
            return
        for summary_key in self._summary_keys_for_delta(delta):
            self.schedule_refresh(summary_key, reason=str((delta or {}).get("op") or "source_mutation"))

    def schedule_all_boot_summaries(self, reason: str = "startup") -> None:
        if self._closed:
            return
        for summary_key in self._all_boot_summary_keys():
            self.schedule_refresh(summary_key, reason=reason)

    def schedule_refresh(self, summary_key: str, *, reason: str = "source_mutation") -> None:
        """Debounce a provider-free stale mark for source mutations/settings."""

        summary_key = str(summary_key or "").strip()
        if not summary_key or self._closed:
            return
        with contextlib.suppress(Exception):
            loop = asyncio.get_running_loop()
            prior = self._tasks.get(summary_key)
            if prior is not None and not prior.done():
                prior.cancel()
            self._tasks[summary_key] = loop.create_task(
                self._debounced_mark_stale(
                    summary_key,
                    str(reason or "source_mutation"),
                )
            )

    async def refresh(self, summary_key: str) -> dict:
        """Refresh one summary cache row, calling the provider only if needed."""

        summary_key = str(summary_key or "").strip()
        if not summary_key:
            return _public_summary_row({})
        if summary_key in self._refreshing:
            return self.cached_summary(summary_key)
        self._refreshing.add(summary_key)
        try:
            return await self._refresh_locked(summary_key)
        finally:
            self._refreshing.discard(summary_key)

    async def shutdown(self) -> None:
        self._closed = True
        tasks = [
            task
            for task in [*self._tasks.values(), *self._read_tasks.values()]
            if task is not None
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._read_tasks.clear()

    async def _mark_stale_task(self, summary_key: str) -> None:
        with contextlib.suppress(Exception):
            self.mark_stale_if_needed(summary_key)

    def _downgrade_stranded_refreshing_row(self, row: dict) -> dict:
        summary_key = str((row or {}).get("summary_key", "") or "").strip()
        summary_text = str((row or {}).get("summary_text", "") or "")
        status = "stale" if summary_text.strip() else "empty"
        updated = {
            **dict(row or {}),
            "status": status,
            "error": (
                "Previous AI boot-summary refresh was interrupted; refresh "
                "will retry lazily on the next read."
            ),
        }
        if summary_key:
            with contextlib.suppress(Exception):
                return self._upsert_summary(updated)
        return updated

    async def _debounced_mark_stale(
        self,
        summary_key: str,
        reason: str,
    ) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
            if self._closed:
                return
            await self._mark_stale_task(summary_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("AI boot-summary stale mark failed (%s, %s)", summary_key, reason)

    async def _read_refresh_task(self, summary_key: str) -> None:
        try:
            if self._closed:
                return
            await self.refresh(summary_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("AI boot-summary read refresh failed (%s)", summary_key)
        finally:
            task = self._read_tasks.get(summary_key)
            if task is asyncio.current_task():
                self._read_tasks.pop(summary_key, None)

    def _min_interval_seconds(self) -> float:
        settings = getattr(self.state, "global_settings", None)
        return max(
            0.0,
            _float(
                getattr(
                    settings,
                    "ai_boot_summary_min_interval_seconds",
                    DEFAULT_MIN_INTERVAL_SECONDS,
                )
            ),
        )

    def _max_refreshes_per_hour(self) -> int:
        settings = getattr(self.state, "global_settings", None)
        try:
            value = int(
                getattr(
                    settings,
                    "ai_boot_summary_max_refreshes_per_hour",
                    DEFAULT_MAX_REFRESHES_PER_HOUR,
                )
            )
        except (TypeError, ValueError):
            value = DEFAULT_MAX_REFRESHES_PER_HOUR
        return max(0, value)

    def _reset_hourly_window_if_needed(self, *, now: float) -> None:
        if now - self._hour_window_started_at >= REFRESH_CAP_WINDOW_SECONDS:
            self._hour_window_started_at = now
            self._hour_attempts = 0

    def _consume_hourly_attempt(self, *, now: float) -> bool:
        self._reset_hourly_window_if_needed(now=now)
        maximum = self._max_refreshes_per_hour()
        if self._hour_attempts >= maximum:
            return False
        self._hour_attempts += 1
        return True

    def _hourly_cap_exhausted(self, *, now: float) -> bool:
        self._reset_hourly_window_if_needed(now=now)
        return self._hour_attempts >= self._max_refreshes_per_hour()

    def _cap_message(self, *, now: float) -> str:
        self._reset_hourly_window_if_needed(now=now)
        remaining = max(
            1.0,
            REFRESH_CAP_WINDOW_SECONDS - (now - self._hour_window_started_at),
        )
        return (
            "AI boot-summary refresh is cost-capped for this daemon; returning "
            f"cached context and backing off for about {int(remaining)}s."
        )

    def _cooldown_message(self, summary_key: str, *, now: float) -> str:
        until = max(
            float(self._global_cooldown_until or 0.0),
            float(self._scope_cooldown_until.get(summary_key, 0.0) or 0.0),
        )
        if until <= now:
            return ""
        remaining = max(1, int(until - now))
        return (
            "AI boot-summary refresh is rate-limited; returning cached "
            f"context while backing off for about {remaining}s."
        )

    def _record_gate_message(self, summary_key: str, message: str) -> None:
        summary_key = str(summary_key or "").strip()
        message = str(message or "").strip()
        if not summary_key or not message:
            return
        with contextlib.suppress(Exception):
            row = self._load_summary(summary_key)
            if not row:
                return
            status = str(row.get("status", "") or "empty").strip()
            if status not in {"stale", "empty"}:
                return
            self._upsert_summary({**row, "status": status, "error": message})

    def _record_rate_limit_failure(
        self,
        summary_key: str,
        failure: LLMFailure,
    ) -> str:
        now = time.monotonic()
        retry_after = getattr(failure, "retry_after_seconds", None)
        try:
            retry_after_seconds = float(retry_after)
        except (TypeError, ValueError):
            retry_after_seconds = 0.0
        if retry_after_seconds <= 0:
            prior = float(self._scope_backoff_seconds.get(summary_key, 0.0) or 0.0)
            retry_after_seconds = (
                min(RATE_LIMIT_MAX_BACKOFF_SECONDS, prior * 2.0)
                if prior
                else RATE_LIMIT_INITIAL_BACKOFF_SECONDS
            )
        retry_after_seconds = max(
            1.0,
            min(RATE_LIMIT_MAX_BACKOFF_SECONDS, retry_after_seconds),
        )
        self._scope_backoff_seconds[summary_key] = retry_after_seconds
        self._scope_cooldown_until[summary_key] = max(
            float(self._scope_cooldown_until.get(summary_key, 0.0) or 0.0),
            now + retry_after_seconds,
        )
        self._global_backoff_seconds = max(
            float(self._global_backoff_seconds or 0.0),
            retry_after_seconds,
        )
        self._global_cooldown_until = max(
            float(self._global_cooldown_until or 0.0),
            now + retry_after_seconds,
        )
        return (
            "AI boot-summary refresh is rate-limited; returning cached "
            f"context while backing off for about {int(retry_after_seconds)}s."
        )

    def _clear_rate_limit_state(self, summary_key: str) -> None:
        self._scope_cooldown_until.pop(summary_key, None)
        self._scope_backoff_seconds.pop(summary_key, None)

    def mark_stale_if_needed(self, summary_key: str) -> dict:
        """Compute current source hash and mark cached rows stale/empty.

        This method never calls an LLM provider.
        """

        bundle = self._build_bundle(summary_key)
        if bundle is None:
            return self.cached_summary(summary_key)
        prev = self._load_summary(summary_key)
        if prev is None:
            return self._upsert_summary({
                "summary_key": bundle.summary_key,
                "summary_type": bundle.summary_type,
                "scope_kind": bundle.scope_kind,
                "scope_ref": bundle.scope_ref,
                "provider": bundle.provider,
                "model": bundle.model,
                "prompt_version": bundle.prompt_version,
                "source_hash": bundle.source_hash,
                "source_counts": bundle.source_counts,
                "summary_text": "",
                "status": "empty",
                "generated_at": 0,
                "error": "",
            })
        if str(prev.get("status", "") or "").strip() == "refreshing":
            prev = self._downgrade_stranded_refreshing_row(prev)
        changed = (
            str(prev.get("source_hash", "") or "") != bundle.source_hash
            or str(prev.get("provider", "") or "") != bundle.provider
            or str(prev.get("model", "") or "") != bundle.model
            or str(prev.get("prompt_version", "") or "") != bundle.prompt_version
        )
        if not changed:
            return prev
        status = "stale" if str(prev.get("summary_text", "") or "").strip() else "empty"
        return self._upsert_summary({
            **prev,
            "summary_key": bundle.summary_key,
            "summary_type": bundle.summary_type,
            "scope_kind": bundle.scope_kind,
            "scope_ref": bundle.scope_ref,
            "provider": bundle.provider,
            "model": bundle.model,
            "prompt_version": bundle.prompt_version,
            "source_hash": bundle.source_hash,
            "source_counts": bundle.source_counts,
            "status": status,
            "error": "",
        })

    async def _refresh_locked(self, summary_key: str) -> dict:
        bundle = self._build_bundle(summary_key)
        if bundle is None:
            return self.cached_summary(summary_key)
        prev = self._load_summary(summary_key)

        if not self._boot_summary_generation_enabled():
            status = "stale" if prev and str(prev.get("summary_text", "") or "").strip() else "empty"
            return self._upsert_summary({
                **(prev or {}),
                "summary_key": bundle.summary_key,
                "summary_type": bundle.summary_type,
                "scope_kind": bundle.scope_kind,
                "scope_ref": bundle.scope_ref,
                "provider": bundle.provider,
                "model": bundle.model,
                "prompt_version": bundle.prompt_version,
                "source_hash": bundle.source_hash,
                "source_counts": bundle.source_counts,
                "status": status,
                "error": "AI boot-summary generation is disabled.",
            })

        fresh = _is_fresh_ready(prev, bundle)
        if fresh:
            return prev

        if not bundle.sources:
            return self._upsert_summary({
                **(prev or {}),
                "summary_key": bundle.summary_key,
                "summary_type": bundle.summary_type,
                "scope_kind": bundle.scope_kind,
                "scope_ref": bundle.scope_ref,
                "provider": bundle.provider,
                "model": bundle.model,
                "prompt_version": bundle.prompt_version,
                "source_hash": bundle.source_hash,
                "source_counts": bundle.source_counts,
                "summary_text": "",
                "status": "empty",
                "generated_at": 0,
                "error": "No source material is available.",
            })

        previous_summary = str((prev or {}).get("summary_text", "") or "").strip()
        use_delta, source_text = _build_delta_text(prev, bundle)
        if not use_delta:
            source_text = _format_sources(bundle.sources, max_chars=MAX_FULL_SOURCE_CHARS)

        refreshing = self._upsert_summary({
            **(prev or {}),
            "summary_key": bundle.summary_key,
            "summary_type": bundle.summary_type,
            "scope_kind": bundle.scope_kind,
            "scope_ref": bundle.scope_ref,
            "provider": bundle.provider,
            "model": bundle.model,
            "prompt_version": bundle.prompt_version,
            "source_hash": bundle.source_hash,
            "source_counts": bundle.source_counts,
            "status": "refreshing",
            "error": "",
        })

        result = await self._summarize_source(
            bundle,
            source_text,
            previous_summary=previous_summary if use_delta else "",
            incremental=use_delta,
        )
        if isinstance(result, LLMFailure):
            message = str(result.message or "AI summary generation failed.")
            if int(result.status_code or 0) == 429:
                message = self._record_rate_limit_failure(bundle.summary_key, result)
            return self._failure_row(bundle, refreshing, message)
        if not isinstance(result, LLMResult):
            return self._failure_row(
                bundle,
                refreshing,
                "AI summary generation returned an invalid response.",
            )
        summary_text = str(result.text or "").strip()
        if not summary_text:
            return self._failure_row(
                bundle,
                refreshing,
                "AI summary generation returned empty text.",
            )

        current_bundle = self._build_bundle(summary_key)
        if current_bundle is None:
            return self.cached_summary(summary_key)
        if not _same_bundle_version(bundle, current_bundle):
            return self._source_changed_during_refresh_row(
                current_bundle,
                self._load_summary(summary_key) or refreshing,
            )

        now = time.time()
        self._clear_rate_limit_state(bundle.summary_key)
        return self._upsert_summary({
            "summary_key": bundle.summary_key,
            "summary_type": bundle.summary_type,
            "scope_kind": bundle.scope_kind,
            "scope_ref": bundle.scope_ref,
            "provider": result.provider or bundle.provider,
            "model": result.model or bundle.model,
            "prompt_version": bundle.prompt_version,
            "source_hash": bundle.source_hash,
            "source_counts": bundle.source_counts,
            "summary_text": summary_text,
            "status": "ready",
            "generated_at": now,
            "updated_at": now,
            "error": "",
        })

    def _source_changed_during_refresh_row(
        self,
        bundle: SummarySourceBundle,
        prev: dict | None,
    ) -> dict:
        summary_text = str((prev or {}).get("summary_text", "") or "")
        if not bundle.sources:
            summary_text = ""
        status = "stale" if summary_text.strip() else "empty"
        error = (
            "Source material changed during AI boot-summary refresh; "
            "refresh will retry lazily on the next read."
        )
        if not bundle.sources:
            error = "No source material is available."
        return self._upsert_summary({
            **(prev or {}),
            "summary_key": bundle.summary_key,
            "summary_type": bundle.summary_type,
            "scope_kind": bundle.scope_kind,
            "scope_ref": bundle.scope_ref,
            "provider": bundle.provider,
            "model": bundle.model,
            "prompt_version": bundle.prompt_version,
            "source_hash": bundle.source_hash,
            "source_counts": bundle.source_counts,
            "summary_text": summary_text,
            "status": status,
            "error": error,
        })

    async def _summarize_source(
        self,
        bundle: SummarySourceBundle,
        source_text: str,
        *,
        previous_summary: str = "",
        incremental: bool = False,
    ) -> ai.LLMResponse:
        client = self.llm_client or LLMClient(state=self.state, db=self.db)
        chunks = _chunk_text(source_text, CHUNK_TARGET_CHARS, MAX_CHUNKS)
        instructions = _summary_instructions(bundle.summary_type, incremental=incremental)
        if len(chunks) <= 1:
            return await self.summarize_func(
                purpose=f"{bundle.summary_type}.boot_summary",
                source_text=source_text,
                instructions=instructions,
                max_tokens=1200,
                cache_key=f"{bundle.summary_type}:{bundle.scope_ref}:{PROMPT_VERSION}",
                previous_summary=previous_summary,
                client=client,
            )

        partials: list[str] = []
        chunk_instructions = (
            instructions
            + "\nSummarize this chunk only; preserve durable decisions, plans, "
              "checkpoints, open questions, and stale risks."
        )
        for idx, chunk in enumerate(chunks, start=1):
            result = await self.summarize_func(
                purpose=f"{bundle.summary_type}.boot_summary.chunk",
                source_text=f"Chunk {idx}/{len(chunks)}\n\n{chunk}",
                instructions=chunk_instructions,
                max_tokens=800,
                cache_key=f"{bundle.summary_type}:{bundle.scope_ref}:{PROMPT_VERSION}:chunk",
                previous_summary="",
                client=client,
            )
            if isinstance(result, LLMFailure):
                return result
            if not isinstance(result, LLMResult) or not str(result.text or "").strip():
                return LLMFailure(
                    kind="provider_error",
                    message="AI summary chunk generation returned empty text.",
                    provider=bundle.provider,
                    model=bundle.model,
                )
            partials.append(str(result.text or "").strip())
        return await self.summarize_func(
            purpose=f"{bundle.summary_type}.boot_summary.final",
            source_text="\n\n".join(
                f"Chunk summary {idx}:\n{text}"
                for idx, text in enumerate(partials, start=1)
            ),
            instructions=instructions,
            max_tokens=1200,
            cache_key=f"{bundle.summary_type}:{bundle.scope_ref}:{PROMPT_VERSION}:final",
            previous_summary=previous_summary if incremental else "",
            client=client,
        )

    def _failure_row(
        self,
        bundle: SummarySourceBundle,
        prev: dict | None,
        message: str,
    ) -> dict:
        summary_text = str((prev or {}).get("summary_text", "") or "")
        status = "stale" if summary_text.strip() else "empty"
        return self._upsert_summary({
            **(prev or {}),
            "summary_key": bundle.summary_key,
            "summary_type": bundle.summary_type,
            "scope_kind": bundle.scope_kind,
            "scope_ref": bundle.scope_ref,
            "provider": bundle.provider,
            "model": bundle.model,
            "prompt_version": bundle.prompt_version,
            "source_hash": bundle.source_hash,
            "source_counts": bundle.source_counts,
            "summary_text": summary_text,
            "status": status,
            "error": str(message or "AI summary generation failed."),
        })

    def _build_bundle(self, summary_key: str) -> SummarySourceBundle | None:
        summary_type, scope_kind, scope_ref = parse_summary_key(summary_key)
        if not summary_type or not scope_ref:
            return None
        provider, model = _provider_model(self.state)
        if summary_type == ARCHITECT_BOOT_TYPE:
            sources = self._architect_sources(scope_ref)
        elif summary_type == ENGINEER_BOOT_TYPE:
            sources = self._engineer_sources(scope_ref)
        else:
            return None
        source_counts = _source_counts(sources)
        source_hash = _aggregate_source_hash(
            prompt_version=PROMPT_VERSION,
            provider=provider,
            model=model,
            sources=sources,
        )
        return SummarySourceBundle(
            summary_key=summary_key,
            summary_type=summary_type,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            provider=provider,
            model=model,
            prompt_version=PROMPT_VERSION,
            source_hash=source_hash,
            source_counts=source_counts,
            sources=sources,
        )

    def _architect_sources(self, architect_id: str) -> list[SummarySource]:
        state = self.state
        if state is None:
            return []
        journal_reader = getattr(state, "architect_journal_read", None)
        entries = []
        if callable(journal_reader):
            with contextlib.suppress(Exception):
                entries = journal_reader(architect_id, limit=MAX_SOURCE_ROWS) or []
        entries = list(reversed(list(entries)))
        sources: list[SummarySource] = []
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id", "") or "") or f"entry-{index}"
            timestamp = _float(entry.get("timestamp", 0))
            entry_type = str(entry.get("type", "") or "").strip() or "observation"
            text = _stable_json({
                "timestamp": timestamp,
                "type": entry_type,
                "entry": str(entry.get("entry", "") or ""),
            })
            sources.append(_source(
                source_key=f"architect_journal:{architect_id}:{entry_id}",
                source_type="architect_journal",
                title=f"architect journal {entry_type} {entry_id}",
                text=text,
            ))

        decision_loader = getattr(state, "load_decisions_for_architect", None)
        decisions = []
        if callable(decision_loader):
            with contextlib.suppress(Exception):
                decisions = decision_loader(architect_id, include_archived=True) or []
        decisions = sorted(
            [dict(item) for item in decisions if isinstance(item, dict)],
            key=lambda item: (
                _float(item.get("created_at", 0)),
                _float(item.get("updated_at", 0)),
                str(item.get("id", "") or ""),
            ),
        )
        for decision in decisions[:MAX_SOURCE_ROWS]:
            decision_id = str(decision.get("id", "") or "").strip()
            if not decision_id:
                continue
            text = _stable_json({
                "title": str(decision.get("title", "") or ""),
                "status": str(decision.get("status", "") or ""),
                "rationale": str(decision.get("rationale", "") or ""),
                "supersedes": decision.get("supersedes"),
                "linked_task_ids": list(decision.get("linked_task_ids", []) or []),
                "linked_engineer_ids": list(decision.get("linked_engineer_ids", []) or []),
                "archived": bool(decision.get("archived", False)),
                "created_at": _float(decision.get("created_at", 0)),
                "updated_at": _float(decision.get("updated_at", 0)),
            })
            sources.append(_source(
                source_key=f"decision:{decision_id}",
                source_type="decision",
                title=f"decision {decision_id} ({decision.get('status', '')})",
                text=text,
            ))
        return _ordered_sources(sources)

    def _engineer_sources(self, engineer_id: str) -> list[SummarySource]:
        state = self.state
        if state is None:
            return []
        engineer = getattr(state, "agents", {}).get(engineer_id)
        group = str(getattr(engineer, "group", "") or "").strip()
        if not group:
            return []
        reader = getattr(state, "journal_read", None)
        entries = []
        if callable(reader):
            with contextlib.suppress(Exception):
                entries = reader(
                    group,
                    MAX_SOURCE_ROWS,
                    "",
                    author_cell_id=engineer_id,
                ) or []
        entries = [
            dict(entry)
            for entry in reversed(list(entries))
            if isinstance(entry, dict)
            and str(entry.get("type", "") or "").strip()
            in {"decision", "plan", "checkpoint"}
        ]
        sources: list[SummarySource] = []
        for index, entry in enumerate(entries, start=1):
            entry_id = str(entry.get("id", "") or "") or f"entry-{index}"
            entry_type = str(entry.get("type", "") or "").strip()
            text = _stable_json({
                "timestamp": _float(entry.get("timestamp", 0)),
                "type": entry_type,
                "entry": str(entry.get("entry", "") or ""),
            })
            sources.append(_source(
                source_key=f"engineer_journal:{engineer_id}:{entry_id}",
                source_type="engineer_journal",
                title=f"engineer journal {entry_type} {entry_id}",
                text=text,
            ))
        return _ordered_sources(sources)

    def _summary_keys_for_delta(self, delta: dict) -> list[str]:
        op = str((delta or {}).get("op", "") or "")
        if op == "architect_journal_append":
            architect_id = str((delta or {}).get("architect_id", "") or "").strip()
            return [architect_boot_summary_key(architect_id)] if architect_id else []
        if op in {"decision_upsert", "decision_remove"}:
            architect_id = str((delta or {}).get("architect_id", "") or "").strip()
            if architect_id:
                return [architect_boot_summary_key(architect_id)]
            return [
                key for key in self._all_boot_summary_keys()
                if key.startswith(f"{ARCHITECT_BOOT_TYPE}:")
            ]
        if op == "journal_append":
            engineer_id = str((delta or {}).get("author_cell_id", "") or "").strip()
            if engineer_id:
                return [engineer_boot_summary_key(engineer_id)]
            group = str((delta or {}).get("group", "") or "").strip()
            return self._engineer_keys_for_group(group)
        return []

    def _all_boot_summary_keys(self) -> list[str]:
        state = self.state
        if state is None:
            return []
        keys: list[str] = []
        for agent_id, cell in getattr(state, "agents", {}).items():
            kind = str(getattr(cell, "kind", "") or "").strip()
            if kind == "architect":
                keys.append(architect_boot_summary_key(agent_id))
            elif kind == "engineer":
                keys.append(engineer_boot_summary_key(agent_id))
        return keys

    def _engineer_keys_for_group(self, group: str) -> list[str]:
        group = str(group or "").strip()
        state = self.state
        if state is None or not group:
            return []
        return [
            engineer_boot_summary_key(agent_id)
            for agent_id, cell in getattr(state, "agents", {}).items()
            if str(getattr(cell, "kind", "") or "").strip() == "engineer"
            and str(getattr(cell, "group", "") or "").strip() == group
        ]

    def _boot_summary_generation_enabled(self) -> bool:
        settings = getattr(self.state, "global_settings", None)
        if settings is None:
            return True
        return bool(getattr(settings, "ai_enabled", False)) and bool(
            getattr(settings, "ai_boot_summary_enabled", True)
        )

    def _load_summary(self, summary_key: str) -> dict | None:
        if self.db is None:
            return None
        return self.db.ai_load_summary(summary_key)

    def _upsert_summary(self, row: dict) -> dict:
        if self.db is None:
            return dict(row or {})
        return self.db.ai_upsert_summary(row)


def architect_boot_summary_key(architect_id: str) -> str:
    return f"{ARCHITECT_BOOT_TYPE}:{str(architect_id or '').strip()}"


def engineer_boot_summary_key(engineer_id: str) -> str:
    return f"{ENGINEER_BOOT_TYPE}:{str(engineer_id or '').strip()}"


def parse_summary_key(summary_key: str) -> tuple[str, str, str]:
    text = str(summary_key or "").strip()
    prefix, sep, scope_ref = text.partition(":")
    if sep != ":" or not scope_ref:
        return "", "", ""
    if prefix == ARCHITECT_BOOT_TYPE:
        return ARCHITECT_BOOT_TYPE, "architect", scope_ref
    if prefix == ENGINEER_BOOT_TYPE:
        return ENGINEER_BOOT_TYPE, "engineer", scope_ref
    return "", "", ""


def cached_boot_summary_payload(state, caller_kind: str, caller_id: str) -> dict:
    """Return the cached boot summary payload for an MCP read tool.

    This helper never performs a live provider call inline.  A stale cached row
    may schedule one gated out-of-band refresh for a later read.
    """

    caller_kind = str(caller_kind or "").strip()
    caller_id = str(caller_id or "").strip()
    if caller_kind == "architect":
        summary_key = architect_boot_summary_key(caller_id)
        expected_type = ARCHITECT_BOOT_TYPE
    else:
        summary_key = engineer_boot_summary_key(caller_id)
        expected_type = ENGINEER_BOOT_TYPE

    service = getattr(state, "ai_summary_service", None)
    if service is not None and callable(getattr(service, "cached_summary_for_read", None)):
        row = service.cached_summary_for_read(summary_key)
    elif service is not None and callable(getattr(service, "cached_summary", None)):
        row = service.cached_summary(summary_key)
    else:
        db = getattr(state, "db", None)
        cached = None
        if db is not None:
            with contextlib.suppress(Exception):
                cached = db.ai_load_summary(summary_key)
        row = _public_summary_row(cached or {
            "summary_key": summary_key,
            "summary_type": expected_type,
            "scope_kind": caller_kind,
            "scope_ref": caller_id,
            "prompt_version": PROMPT_VERSION,
            "status": "empty",
        })

    settings = getattr(state, "global_settings", None)
    if settings is not None and (
        not bool(getattr(settings, "ai_enabled", False))
        or not bool(getattr(settings, "ai_boot_summary_enabled", True))
    ):
        row = dict(row)
        row["status"] = "empty"
        row["summary"] = ""
        row["message"] = (
            "AI boot summaries are disabled; use the raw journal/decision "
            "recovery tools."
        )
    return row


def _is_fresh_ready(prev: dict | None, bundle: SummarySourceBundle) -> bool:
    return bool(
        prev
        and str(prev.get("status", "") or "") == "ready"
        and str(prev.get("source_hash", "") or "") == bundle.source_hash
        and str(prev.get("provider", "") or "") == bundle.provider
        and str(prev.get("model", "") or "") == bundle.model
        and str(prev.get("prompt_version", "") or "") == bundle.prompt_version
        and str(prev.get("summary_text", "") or "").strip()
    )


def _same_bundle_version(
    left: SummarySourceBundle,
    right: SummarySourceBundle,
) -> bool:
    return bool(
        left.summary_key == right.summary_key
        and left.summary_type == right.summary_type
        and left.scope_kind == right.scope_kind
        and left.scope_ref == right.scope_ref
        and left.provider == right.provider
        and left.model == right.model
        and left.prompt_version == right.prompt_version
        and left.source_hash == right.source_hash
    )


def _synthetic_summary_row(summary_key: str) -> dict:
    summary_type, scope_kind, scope_ref = parse_summary_key(summary_key)
    return {
        "summary_key": summary_key,
        "summary_type": summary_type,
        "scope_kind": scope_kind,
        "scope_ref": scope_ref,
        "provider": "",
        "model": "",
        "prompt_version": PROMPT_VERSION,
        "source_hash": "",
        "source_counts": {},
        "summary_text": "",
        "status": "empty",
        "generated_at": 0,
        "updated_at": 0,
        "error": "",
    }


def _row_has_sources(row: dict | None) -> bool:
    counts = (row or {}).get("source_counts", {})
    if not isinstance(counts, dict):
        return False
    try:
        if int(counts.get("total", 0) or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    by_type = counts.get("by_type", {})
    if isinstance(by_type, dict):
        for value in by_type.values():
            with contextlib.suppress(TypeError, ValueError):
                if int(value or 0) > 0:
                    return True
    return False


def _build_delta_text(
    prev: dict | None,
    bundle: SummarySourceBundle,
) -> tuple[bool, str]:
    if not prev or not str(prev.get("summary_text", "") or "").strip():
        return False, ""
    counts = prev.get("source_counts", {}) if isinstance(prev.get("source_counts", {}), dict) else {}
    prev_hashes = counts.get("content_hashes", {})
    if not isinstance(prev_hashes, dict) or not prev_hashes:
        return False, ""
    current_hashes = {
        source.source_key: source.content_hash
        for source in bundle.sources
    }
    changed = [
        source
        for source in bundle.sources
        if str(prev_hashes.get(source.source_key, "") or "") != source.content_hash
    ]
    removed = sorted(set(prev_hashes) - set(current_hashes))
    if len(changed) + len(removed) > MAX_DELTA_ROWS:
        return False, ""
    changed_text = _format_sources(changed, max_chars=MAX_DELTA_SOURCE_CHARS)
    if not changed_text and not removed:
        return False, ""
    if len(changed_text) > MAX_DELTA_SOURCE_CHARS:
        return False, ""
    parts = []
    if changed_text:
        parts.append("Changed or new source rows:\n" + changed_text)
    if removed:
        parts.append("Removed source keys:\n" + "\n".join(f"- {key}" for key in removed))
    return True, "\n\n".join(parts)


def _source(
    *,
    source_key: str,
    source_type: str,
    title: str,
    text: str,
) -> SummarySource:
    content_hash = hashlib.sha256(
        _stable_json({
            "source_key": source_key,
            "source_type": source_type,
            "title": title,
            "text": text,
        }).encode("utf-8")
    ).hexdigest()
    return SummarySource(
        source_key=source_key,
        source_type=source_type,
        title=title,
        text=text,
        content_hash=content_hash,
    )


def _ordered_sources(sources: list[SummarySource]) -> list[SummarySource]:
    return sorted(sources, key=lambda item: (item.source_type, item.source_key))


def _source_counts(sources: list[SummarySource]) -> dict:
    by_type: dict[str, int] = {}
    decisions_by_status: dict[str, int] = {}
    content_hashes: dict[str, str] = {}
    for source in sources:
        by_type[source.source_type] = by_type.get(source.source_type, 0) + 1
        content_hashes[source.source_key] = source.content_hash
        if source.source_type == "decision":
            with contextlib.suppress(Exception):
                data = json.loads(source.text)
                status = str(data.get("status", "") or "").strip() or "unknown"
                decisions_by_status[status] = decisions_by_status.get(status, 0) + 1
    counts = {
        "total": len(sources),
        "by_type": by_type,
        "content_hashes": content_hashes,
    }
    if decisions_by_status:
        counts["decisions_by_status"] = decisions_by_status
    return counts


def _aggregate_source_hash(
    *,
    prompt_version: str,
    provider: str,
    model: str,
    sources: list[SummarySource],
) -> str:
    parts = [
        str(prompt_version or ""),
        str(provider or ""),
        str(model or ""),
    ]
    parts.extend(source.content_hash for source in sources)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _format_sources(sources: list[SummarySource], *, max_chars: int) -> str:
    remaining = max(0, int(max_chars or 0))
    parts: list[str] = []
    for source in sources:
        if remaining <= 0:
            break
        text = (
            f"## {source.title}\n"
            f"source_key: {source.source_key}\n"
            f"content_hash: {source.content_hash}\n"
            f"{source.text.strip()}\n"
        )
        if len(text) > remaining:
            text = text[: max(0, remaining - 32)].rstrip() + "\n[truncated]\n"
        parts.append(text)
        remaining -= len(text)
    return "\n".join(parts).strip()


def _chunk_text(text: str, target_chars: int, max_chunks: int) -> list[str]:
    text = str(text or "")
    target = max(1_000, int(target_chars or CHUNK_TARGET_CHARS))
    max_count = max(1, int(max_chunks or MAX_CHUNKS))
    if len(text) <= target:
        return [text]
    chunks = [text[idx: idx + target] for idx in range(0, len(text), target)]
    if len(chunks) <= max_count:
        return chunks
    head = chunks[: max_count - 1]
    tail = "\n[truncated middle]\n" + chunks[-1]
    return head + [tail[-target:]]


def _summary_instructions(summary_type: str, *, incremental: bool) -> str:
    base = (
        "You maintain a compact Torque boot-recovery summary. "
        "Return concise Markdown bullets only. Preserve durable decisions, "
        "plans, checkpoints, open questions, stale risks, owners, and linked "
        "task/engineer ids when present. Do not invent facts. If source "
        "material is thin, say so explicitly."
    )
    if summary_type == ARCHITECT_BOOT_TYPE:
        base += (
            " This is for an Architect. Summarize Architect journal entries "
            "and the Architect's own decision log grouped by status. Call out "
            "open questions and stale risks inferred from proposed/revised "
            "decisions or journal text."
        )
    else:
        base += (
            " This is for an Engineer. Summarize only that Engineer's own "
            "journal decision/plan/checkpoint map. Do not refer to or assume "
            "access to Architect decision logs."
        )
    if incremental:
        base += (
            " Update the previous summary with the changed/new/deleted source "
            "delta. Keep stable facts from the previous summary when the delta "
            "does not supersede them."
        )
    return base


def _provider_model(state) -> tuple[str, str]:
    settings = getattr(state, "global_settings", None)
    provider = str(getattr(settings, "ai_generation_provider", "anthropic") or "anthropic").strip()
    if provider == "openai_compatible":
        model = str(getattr(settings, "ai_openai_compatible_model", "") or "").strip()
    elif provider == "anthropic":
        model = str(getattr(settings, "ai_anthropic_model", "") or "").strip()
    else:
        model = ""
    return provider, model


def _public_summary_row(row: dict | None) -> dict:
    row = dict(row or {})
    status = str(row.get("status", "") or "empty").strip()
    if status not in SUMMARY_STATUSES:
        status = "empty"
    summary_text = str(row.get("summary_text", "") or "")
    counts = row.get("source_counts", {})
    if not isinstance(counts, dict):
        counts = {}
    public_counts = {
        key: value
        for key, value in counts.items()
        if key != "content_hashes"
    }
    message = _status_message(status, bool(summary_text.strip()), str(row.get("error", "") or ""))
    return {
        "type": str(row.get("summary_type", "") or ""),
        "summary_key": str(row.get("summary_key", "") or ""),
        "status": status,
        "summary": summary_text,
        "source_counts": public_counts,
        "generated_at": _float(row.get("generated_at", 0)),
        "source_hash": str(row.get("source_hash", "") or ""),
        "message": message,
    }


def _status_message(status: str, has_summary: bool, error: str) -> str:
    if status == "ready":
        return "Cached boot summary is ready."
    if status == "refreshing":
        return (
            "Cached boot summary is refreshing out-of-band; use raw "
            "journal/decision recovery tools for authoritative context."
        )
    if status == "stale":
        suffix = f" Last refresh error: {error}" if error else ""
        return (
            "Cached boot summary is stale; use raw journal/decision recovery "
            "tools for authoritative context."
            + suffix
        )
    if status == "error":
        return (
            "Cached boot summary has an error; use raw journal/decision "
            "recovery tools."
            + (f" Error: {error}" if error else "")
        )
    if error:
        return (
            "No cached boot summary is available; use raw journal/decision "
            "recovery tools. Last refresh error: "
            + error
        )
    if has_summary:
        return (
            "Cached boot summary is not ready; use raw journal/decision "
            "recovery tools."
        )
    return (
        "No cached boot summary is available; use raw journal/decision "
        "recovery tools."
    )


def _stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "AISummaryService",
    "ARCHITECT_BOOT_TYPE",
    "ENGINEER_BOOT_TYPE",
    "PROMPT_VERSION",
    "architect_boot_summary_key",
    "cached_boot_summary_payload",
    "engineer_boot_summary_key",
    "parse_summary_key",
]
