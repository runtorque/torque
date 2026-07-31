"""Message-loop, schedule, snapshot, broadcast, and recompute runtime behavior."""

from __future__ import annotations

from .state import (
    AgentMessageLoop, Optional, Schedule, _ENGINEER_STREAM_CARD_LIMIT,
    _ENGINEER_STREAM_CONTEXT_LIMIT, _slugify, _unique_slug, asdict, copy,
    asyncio, cloud_hooks, datetime, hot_json_dumps_async,
    log, profiling, time, timezone, uuid, web,
)


# A primary delta frame is sent immediately; this only delays its derived
# engineer-stream follow-up.  350 ms is long enough to collapse the normal
# burst of terminal/event updates while keeping the panel perceptibly current.
_ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS = 0.35
_ENGINEER_RECOMPUTE_HANDOFF_DELAY_SECONDS = 0.001


class StateRuntimeMixin:
    def agent_message_loops_snapshot(self) -> dict[str, dict]:
        """Return persisted user→agent /loop state for UI snapshots."""
        return {
            loop_id: asdict(loop)
            for loop_id, loop in sorted(
                self.agent_message_loops.items(),
                key=lambda item: (
                    str(getattr(item[1], "agent_id", "") or ""),
                    float(getattr(item[1], "updated_at", 0) or 0),
                    str(item[0]),
                ),
            )
        }

    def active_agent_message_loop_for_agent(
        self,
        agent_id: str,
    ) -> AgentMessageLoop | None:
        aid = str(agent_id or "").strip()
        if not aid:
            return None
        active = [
            loop for loop in self.agent_message_loops.values()
            if loop.agent_id == aid and loop.status == "active"
        ]
        if not active:
            return None
        return sorted(
            active,
            key=lambda loop: (float(loop.created_at or 0), loop.id),
        )[-1]

    def agent_message_loop_add(
        self,
        *,
        agent_id: str,
        group_name: str,
        interval_seconds: int,
        message: str,
        created_by: str = "user",
        now: float | None = None,
    ) -> AgentMessageLoop:
        aid = str(agent_id or "").strip()
        if not aid:
            raise ValueError("agent_id is required")
        if self.active_agent_message_loop_for_agent(aid):
            raise ValueError("An active /loop already exists for this agent")
        interval = int(interval_seconds or 0)
        if interval <= 0:
            raise ValueError("interval_seconds must be positive")
        text = str(message or "").strip()
        if not text:
            raise ValueError("loop message is required")
        ts = float(now if now is not None else time.time())
        loop = AgentMessageLoop(
            id="loop-" + uuid.uuid4().hex[:12],
            agent_id=aid,
            group_name=str(group_name or "").strip(),
            interval_seconds=interval,
            message=text,
            status="active",
            created_by=str(created_by or "user").strip() or "user",
            created_at=ts,
            updated_at=ts,
            next_run_at=ts + interval,
        )
        self.agent_message_loops[loop.id] = loop
        self._emit("agent_message_loop_upsert", loop=asdict(loop))
        self._db_save_agent_message_loop(loop)
        return loop

    def agent_message_loop_update(
        self,
        loop_id: str,
        **fields,
    ) -> AgentMessageLoop | None:
        loop = self.agent_message_loops.get(str(loop_id or "").strip())
        if not loop:
            return None
        valid = set(AgentMessageLoop.__dataclass_fields__) - {"id", "created_at"}
        for key, value in fields.items():
            if key in valid:
                setattr(loop, key, value)
        if "updated_at" not in fields:
            loop.updated_at = time.time()
        self._emit("agent_message_loop_upsert", loop=asdict(loop))
        self._db_save_agent_message_loop(loop)
        return loop

    def agent_message_loop_stop(
        self,
        loop_id: str,
        *,
        status: str = "cancelled",
        stopped_by: str = "user",
        reason: str = "",
        now: float | None = None,
    ) -> AgentMessageLoop | None:
        loop = self.agent_message_loops.get(str(loop_id or "").strip())
        if not loop:
            return None
        ts = float(now if now is not None else time.time())
        loop.status = str(status or "cancelled").strip() or "cancelled"
        loop.stopped_by = str(stopped_by or "").strip()
        loop.stop_reason = str(reason or "").strip()
        loop.next_run_at = 0
        loop.deferred_at = 0
        loop.deferred_reason = ""
        loop.updated_at = ts
        self._emit("agent_message_loop_upsert", loop=asdict(loop))
        self._db_save_agent_message_loop(loop)
        return loop

    def due_agent_message_loops(
        self,
        now: float | None = None,
    ) -> list[AgentMessageLoop]:
        ts = float(now if now is not None else time.time())
        return sorted(
            [
                loop for loop in self.agent_message_loops.values()
                if loop.status == "active"
                and float(loop.next_run_at or 0) > 0
                and float(loop.next_run_at or 0) <= ts
            ],
            key=lambda loop: (float(loop.next_run_at or 0), loop.id),
        )

    def _unique_schedule_slug(self, name: str, exclude_id: str = "") -> str:
        base = _slugify(name)
        existing = {s.slug for s in self.schedules.values()
                    if s.id != exclude_id and s.slug}
        return _unique_slug(base, existing)

    def schedule_add(self, name: str, group: str, **kwargs) -> Optional[Schedule]:
        if not name or not group:
            return None
        if group not in self.groups:
            return None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        sid = uuid.uuid4().hex[:8]
        slug = self._unique_schedule_slug(name)
        sched = Schedule(
            id=sid, name=name, slug=slug, group=group,
            created_at=now, updated_at=now,
            **{k: v for k, v in kwargs.items()
               if k in Schedule.__dataclass_fields__ and k not in
               ("id", "name", "slug", "group", "created_at", "updated_at")},
        )
        self.schedules[sid] = sched
        self._emit("schedule_upsert", **asdict(sched))
        self._db_save_schedule(sched)
        return sched

    def schedule_update(self, sid: str, **fields):
        sched = self.schedules.get(sid)
        if not sched:
            return
        valid = set(Schedule.__dataclass_fields__) - {"id", "slug", "created_at"}
        for key, value in fields.items():
            if key in valid:
                setattr(sched, key, value)
        if "name" in fields:
            sched.slug = self._unique_schedule_slug(sched.name, exclude_id=sid)
        from datetime import datetime, timezone
        sched.updated_at = datetime.now(timezone.utc).isoformat()
        self._emit("schedule_upsert", **asdict(sched))
        self._db_save_schedule(sched)

    def schedule_remove(self, sid: str):
        sched = self.schedules.pop(sid, None)
        if sched:
            self._emit("schedule_remove", id=sid)
            self._db_delete_schedule(sid)

    def schedule_get_due(self, now_iso: str) -> list[Schedule]:
        """Return enabled schedules whose next_run_at is <= now."""
        return [s for s in self.schedules.values()
                if s.enabled and s.next_run_at and s.next_run_at <= now_iso]

    async def broadcast(self):
        """Send accumulated deltas to all WS clients.

        WS delivery is explicitly fire-and-forget/best-effort: there is no
        ACK/replay protocol for individual frames. Clients recover from
        disconnects or missed deltas by requesting a fresh durable snapshot;
        high-level events additionally rely on panel_events persistence.

        If there are no delta ops (e.g. broadcast after a focus change
        where the _emit was called), this is a no-op — the delta was
        already queued by _emit().

        Engineer-stream recompute + `git for-each-ref` prefill are deferred
        to a background worker (`_engineer_recompute_worker`) so UI
        mutations don't wait on git. The worker fires a follow-up
        broadcast with the computed `engineer_streams` ops; clients treat
        it as any other delta frame.
        """
        # Cheap pre-lock bail-out: if nothing has been emitted, skip.
        if not self._delta_ops:
            return
        # Collect engineer-stream affected groups before draining ops so
        # the background worker has something to chew on after the
        # primary frame goes out. Fingerprint cache is updated as a
        # side effect (dedupes ephemeral-only agent_upserts).
        engineer_groups = self._collect_engineer_affected_groups(self._delta_ops)
        async with self._ws_clients_lock:
            if not self._delta_ops:
                return
            self._seq += 1
            op_count = len(self._delta_ops)
            ops = self._delta_ops
            self._delta_ops = []
            try:
                msg = await hot_json_dumps_async({
                    "type": "delta", "seq": self._seq,
                    "ops": ops,
                })
            except Exception:
                self._seq -= 1
                self._delta_ops = ops + self._delta_ops
                raise
            client_entries = [
                (ws, self._ws_client_ids.get(ws, ""))
                for ws in self._ws_clients
            ]
            client_focus_by_id: dict[str, dict[str, Optional[str]]] = {}
            if self._ops_include_focus_update(ops):
                for _ws, client_id in client_entries:
                    client_id = str(client_id or "").strip()
                    if not client_id or client_id in client_focus_by_id:
                        continue
                    focus = self.client_focus_state(client_id)
                    if focus is not None:
                        client_focus_by_id[client_id] = focus
        meter = self.metrics_collector
        profiling_enabled = profiling.is_enabled()
        payload_bytes = 0
        if meter.enabled or profiling_enabled:
            payload_bytes = len(msg.encode("utf-8"))
        if meter.enabled:
            meter.record_ws_delta(
                op_count=op_count,
                payload_bytes=payload_bytes,
                subscribers=len(client_entries),
            )
        if profiling_enabled:
            profiling.recorder().observe("ws_delta_payload_bytes", payload_bytes)
            profiling.recorder().observe("ws_delta_ops_count", op_count)
            profiling.recorder().observe("ws_clients_per_broadcast", len(client_entries))
        msg_by_client_id: dict[str, str] = {}
        for client_id, focus in client_focus_by_id.items():
            patched_ops = self._ops_with_client_focus_overlay(ops, focus)
            msg_by_client_id[client_id] = await hot_json_dumps_async({
                "type": "delta", "seq": self._seq,
                "ops": patched_ops,
            })
        # Send to every client concurrently so a slow/stuck client
        # doesn't stall delivery to the others (the lock above already
        # guarantees ordering — only one broadcast is in flight at a time).
        with profiling.timer("ws_broadcast_ms"):
            results = await asyncio.gather(
                *(
                    ws.send_str(msg_by_client_id.get(client_id, msg))
                    for ws, client_id in client_entries
                ),
                return_exceptions=True,
            )
        # Optional cloud connectors observe the SAME already-coalesced delta
        # batch that local WS clients received.  Notification is best-effort and
        # scheduled by cloud_hooks so connector projection/network work can never
        # block local WS delivery.
        cloud_hooks.notify_state_delta_observers(ops, state=self)
        dead: set[web.WebSocketResponse] = {
            ws for (ws, _client_id), result in zip(client_entries, results)
            if isinstance(result, BaseException)
        }
        if dead:
            db = getattr(self, "db", None)
            if db and hasattr(db, "record_mcp_health_event_safe"):
                db.record_mcp_health_event_safe(
                    surface="ws",
                    event="drop",
                    error=f"failed clients: {len(dead)}",
                )
            async with self._ws_clients_lock:
                self._discard_ws_clients_locked(dead)
        if engineer_groups:
            self._schedule_engineer_recompute(engineer_groups)

    def _engineer_stream_counts(self, streams: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for stream in streams:
            state_name = str(stream.get("state", "") or "").strip()
            if not state_name:
                continue
            counts[state_name] = counts.get(state_name, 0) + 1
        return counts

    def _engineer_stream_payload(self, group: str, *, source=None) -> dict:
        from .worktree_streams import compute_worktree_streams

        source = source or self
        try:
            streams = compute_worktree_streams(
                source,
                group=group,
                visibility_limit=_ENGINEER_STREAM_CONTEXT_LIMIT,
                include_orphaned=False,
            )
        except Exception:
            log.exception("Failed to compute engineer streams for %s", group)
            streams = []
        streams = [
            stream for stream in streams
            if str(stream.get("state", "") or "").strip() != "merged"
        ]
        return {
            "count": len(streams),
            "by_state": self._engineer_stream_counts(streams),
            "items": streams[:_ENGINEER_STREAM_CARD_LIMIT],
            "truncated": len(streams) > _ENGINEER_STREAM_CARD_LIMIT,
        }

    def _engineer_stream_compute_snapshot(self):
        """Clone every mutable task/cell input used by stream synthesis.

        The threaded computation must not traverse live ``BoardTask`` or
        ``AgentCell`` instances. A fresh MatrixState retains the exact query
        helpers used by ``compute_worktree_streams`` while this projection
        replaces its mutable inputs with deep copies.
        """
        snapshot = type(self)()
        snapshot.board_tasks = copy.deepcopy(self.board_tasks)
        snapshot.agents = copy.deepcopy(self.agents)
        snapshot.groups = copy.deepcopy(self.groups)
        snapshot.group_settings = copy.deepcopy(self.group_settings)
        snapshot.engineer_settings = copy.deepcopy(self.engineer_settings)
        snapshot._tasks_by_group.clear()
        for task in snapshot.board_tasks.values():
            snapshot._tasks_by_group.setdefault(task.group, set()).add(task.id)
        return snapshot

    def _compute_engineer_stream_payloads(
        self, groups: set[str], *, source=None,
    ) -> list[tuple[str, dict]]:
        """Build stream payloads without mutating the delta accumulator.

        The deferred runtime invokes this CPU-only half in a worker thread;
        keeping emission separate makes `_delta_ops` ownership remain with the
        event loop. The synchronous wrapper above preserves existing callers.
        """
        if not groups:
            return []
        source = source or self
        known_groups = set(source._engineer_stream_groups())
        payloads: list[tuple[str, dict]] = []
        for group in groups:
            if not group:
                continue
            if group not in known_groups:
                payloads.append((group, {
                    "count": 0,
                    "by_state": {},
                    "items": [],
                    "truncated": False,
                }))
                continue
            try:
                payload = self._engineer_stream_payload(group, source=source)
            except Exception:
                log.exception(
                    "engineer stream payload failed for group '%s'", group
                )
                continue
            payloads.append((group, payload))
        return payloads

    def _emit_engineer_stream_payloads(
        self, payloads: list[tuple[str, dict]],
    ) -> bool:
        """Append precomputed payloads without duplicating an open delta."""
        if not payloads:
            return False
        existing_groups = {
            str((op or {}).get("group", "") or "").strip()
            for op in self._delta_ops
            if str((op or {}).get("op", "") or "") in {
                "engineer_streams",
                "engineer_streams_update",
            }
        }
        emitted = False
        for group, payload in payloads:
            if not group or group in existing_groups:
                continue
            self._emit("engineer_streams", group=group, streams=payload)
            emitted = True
        return emitted

    def _schedule_engineer_recompute(self, groups: set[str]) -> None:
        """Queue a deferred engineer-stream recompute for ``groups``.

        Spawns a single worker task. If one is already running, merges
        the new groups into its pending set — the worker re-checks
        after each iteration so it drains everything before exiting.
        """
        groups = {str(group or "").strip() for group in groups}
        groups.discard("")
        self._engineer_recompute_pending |= groups
        if (not self._engineer_recompute_pending
                or self._engineer_recompute_shutdown):
            return
        task = self._engineer_recompute_task
        if (task is not None and not task.done()
                and not task.cancelling()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop; caller is off the async path. Skip silently —
            # the next real broadcast will re-queue anything still dirty.
            return
        worker = loop.create_task(self._engineer_recompute_worker())
        self._engineer_recompute_task = worker
        # A task cancelled before its coroutine first runs never reaches the
        # worker's ``finally``. This callback performs that handoff.
        worker.add_done_callback(self._engineer_recompute_task_done)

    def _engineer_recompute_task_done(self, task) -> None:
        """Recover pending groups when a worker ended before its body ran."""
        if self._engineer_recompute_task is not task:
            return
        self._engineer_recompute_task = None
        self._request_engineer_recompute_handoff()

    def _finish_engineer_recompute_worker(self, task) -> None:
        """Release ``task`` and hand preserved pending work to a successor."""
        if self._engineer_recompute_task is not task:
            return
        self._engineer_recompute_task = None
        self._request_engineer_recompute_handoff()

    def _request_engineer_recompute_handoff(self) -> None:
        """Schedule a successor without creating tasks during loop teardown."""
        if (not self._engineer_recompute_pending
                or self._engineer_recompute_shutdown):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if loop.is_closed():
            return
        handle = self._engineer_recompute_handoff
        if handle is not None and not handle.cancelled():
            return
        # A tiny delayed handle runs on the next live-loop turn, but is not a
        # task. asyncio.run()/IsolatedAsyncioTestCase teardown stops before it
        # fires, avoiding an untracked replacement task after cancellation.
        self._engineer_recompute_handoff = loop.call_later(
            _ENGINEER_RECOMPUTE_HANDOFF_DELAY_SECONDS,
            self._run_engineer_recompute_handoff,
        )

    def _run_engineer_recompute_handoff(self) -> None:
        self._engineer_recompute_handoff = None
        self._schedule_engineer_recompute(set())

    async def shutdown_engineer_recompute(self) -> None:
        """Intentionally discard ephemeral recompute work during shutdown.

        Stream payloads are rebuilt from durable state at next boot. This is
        the sole intentional drop policy, and it prevents cancellation during
        loop teardown from spawning a successor that cannot be awaited.
        """
        self._engineer_recompute_shutdown = True
        handoff = self._engineer_recompute_handoff
        if handoff is not None:
            handoff.cancel()
            self._engineer_recompute_handoff = None
        task = self._engineer_recompute_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._engineer_recompute_task = None
        self._engineer_recompute_pending.clear()

    async def _engineer_recompute_worker(self) -> None:
        """Drain `_engineer_recompute_pending`: prefill branch existence,
        compute stream payloads, broadcast a follow-up delta."""
        from .worktree_streams import prefill_branch_exists_for_state
        worker = asyncio.current_task()
        pending: set[str] = set()
        try:
            # Let a burst of primary broadcasts accumulate before paying for
            # one complete group read-model rebuild. Subsequent iterations do
            # not wait: they exist only for groups dirtied during prefill,
            # threaded computation, or the derived follow-up broadcast.
            await asyncio.sleep(_ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS)
            while self._engineer_recompute_pending:
                pending = self._engineer_recompute_pending
                self._engineer_recompute_pending = set()
                try:
                    await prefill_branch_exists_for_state(self)
                except Exception:
                    log.exception("Branch-exists prefill failed")
                # `compute_worktree_streams` is CPU-heavy and only reads the
                # state here. Keep the state mutation (`_emit`) on the event
                # loop after the thread returns so delta ordering remains
                # single-threaded.
                try:
                    # Snapshot every mutable task/cell input on the event
                    # loop. The thread below only observes this immutable
                    # projection; unlike a generation-discard fence it cannot
                    # starve under a sustained broadcast rate.
                    snapshot = self._engineer_stream_compute_snapshot()
                    payloads = await asyncio.to_thread(
                        self._compute_engineer_stream_payloads,
                        pending,
                        source=snapshot,
                    )
                except Exception:
                    # A threaded read can lose its coherent view if a state
                    # mutation races it. Preserve the dirty signal instead
                    # of making that transient failure the terminal stream
                    # result; back off before retrying a persistent failure.
                    log.exception("Engineer stream payload computation failed")
                    self._engineer_recompute_pending |= pending
                    await asyncio.sleep(_ENGINEER_RECOMPUTE_DEBOUNCE_SECONDS)
                    continue
                if self._emit_engineer_stream_payloads(payloads):
                    # The follow-up broadcast's own _collect_engineer_*
                    # call returns empty (engineer_streams isn't a trigger
                    # op), so this does not recurse into another worker.
                    await self.broadcast()
                pending = set()
        except asyncio.CancelledError:
            # Preserve a local batch drained before cancellation at any await
            # (debounce, prefill, thread, or follow-up broadcast). A running
            # thread cannot be stopped, so its result is deliberately ignored
            # and the successor recomputes this dirty batch.
            self._engineer_recompute_pending |= pending
            raise
        finally:
            self._finish_engineer_recompute_worker(worker)
