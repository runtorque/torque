"""Peer, direct-message, and agent history state behavior."""

from __future__ import annotations

from .state import (
    AGENT_MESSAGE_HISTORY_LIMIT, AGENT_PEER_THREAD_MESSAGE_LIMIT,
    AGENT_PEER_THREAD_SEED_ROW_LIMIT, AgentCell, BoardTask,
    DIRECT_MESSAGE_CACHE_LIMIT, PEER_MESSAGE_CACHE_LIMIT,
    _ENGINEER_WORKLOG_LIMIT, _agent_peer_thread_message_entry,
    _agent_peer_thread_pair_ids, _agent_peer_thread_pair_key,
    _direct_message_cache_entry, _is_agent_peer_thread_row,
    _is_peer_message_cache_entry, _is_user_direct_message_row,
    _peer_message_cache_entry, _peer_message_timestamp, _safe_float,
    _sort_direct_message_cache, _sort_mcp_message_cache,
    cloud_hooks, copy, log, time,
)


class StateMessagesMixin:
    def _upsert_peer_message_cache_entry(
        self,
        cell: AgentCell,
        entry: dict,
        *,
        limit: int = PEER_MESSAGE_CACHE_LIMIT,
    ) -> bool:
        if not cell or not entry or not entry.get("id"):
            return False
        message_id = str(entry.get("id", "") or "").strip()
        before = [dict(item) for item in (cell.mcp_messages or [])]
        kept = [
            dict(item)
            for item in (cell.mcp_messages or [])
            if str((item or {}).get("id", "") or "").strip() != message_id
        ]
        kept.append(dict(entry))
        cell.mcp_messages = _sort_mcp_message_cache(kept)[:max(1, limit)]
        return before != cell.mcp_messages

    def refresh_peer_message_cache_for_agent(
        self,
        agent_id: str,
        *,
        limit: int = PEER_MESSAGE_CACHE_LIMIT,
        emit: bool = True,
    ) -> list[dict]:
        """Rebuild one agent's bounded peer-message UI cache from SQLite."""
        aid = str(agent_id or "").strip()
        cell = self.agents.get(aid)
        if not aid or not cell:
            return []
        if not self.db:
            return [
                dict(entry)
                for entry in (cell.mcp_messages or [])
                if _is_peer_message_cache_entry(entry)
            ][:limit]
        rows = self.db.load_agent_peer_messages_for_agent(aid, limit=limit)
        peer_entries = [
            entry
            for entry in (
                _peer_message_cache_entry(row, aid) for row in rows
            )
            if entry
        ]
        non_peer_entries = [
            dict(entry)
            for entry in (cell.mcp_messages or [])
            if not _is_peer_message_cache_entry(entry)
        ]
        before = [dict(item) for item in (cell.mcp_messages or [])]
        cell.mcp_messages = _sort_mcp_message_cache(
            peer_entries + non_peer_entries
        )[:max(1, limit)]
        if emit and before != cell.mcp_messages:
            self._emit_agent(cell)
        return peer_entries

    def seed_peer_message_caches(
        self,
        *,
        limit: int = PEER_MESSAGE_CACHE_LIMIT,
        emit: bool = False,
    ) -> int:
        """Seed recent Architect/Engineer peer messages after restart/load."""
        seeded = 0
        for cell in list(self.agents.values()):
            if str(getattr(cell, "kind", "") or "").strip() not in {
                "architect",
                "engineer",
            }:
                continue
            entries = self.refresh_peer_message_cache_for_agent(
                cell.id,
                limit=limit,
                emit=emit,
            )
            if entries:
                seeded += 1
        return seeded

    def append_peer_message_to_caches(
        self,
        row: dict,
        *,
        emit: bool = True,
        limit: int = PEER_MESSAGE_CACHE_LIMIT,
    ) -> list[str]:
        """Project a canonical peer-message row into participant caches."""
        changed: list[str] = []
        for agent_id in (
            str((row or {}).get("sender_id", "") or "").strip(),
            str((row or {}).get("recipient_id", "") or "").strip(),
        ):
            if not agent_id or agent_id in changed:
                continue
            cell = self.agents.get(agent_id)
            entry = _peer_message_cache_entry(row or {}, agent_id)
            if not cell or not entry:
                continue
            if self._upsert_peer_message_cache_entry(
                cell,
                entry,
                limit=limit,
            ):
                changed.append(agent_id)
                if emit:
                    self._emit_agent(cell)
        return changed

    def save_peer_message(
        self,
        row: dict,
        *,
        emit: bool = True,
        cache_participants: bool = True,
    ) -> dict | None:
        """Persist a peer message and update bounded live UI/read-model caches."""
        if not self.db:
            return None
        saved = self.db.save_agent_peer_message(row)
        if saved:
            if cache_participants:
                self.append_peer_message_to_caches(saved, emit=emit)
            self.upsert_agent_peer_thread(saved, emit=emit)
        return saved

    def load_peer_messages_for_architect(
        self,
        architect_id: str,
        *,
        limit: int = 100,
        since: float = 0,
        peer_id: str = "",
        thread_id: str = "",
        include_archived: bool = False,
    ) -> list[dict]:
        if not self.db:
            return []
        return self.db.load_peer_messages_for_architect(
            architect_id,
            limit=limit,
            since=since,
            peer_id=peer_id,
            thread_id=thread_id,
            include_archived=include_archived,
        )

    def mark_peer_message_delivered(
        self,
        message_id: str,
        *,
        delivered: bool = True,
        reason: str = "",
        delivered_at: float | None = None,
        emit: bool = True,
        cache_participants: bool = True,
    ) -> dict | None:
        """Persist delivery state and update participant cache entries."""
        if not self.db:
            return None
        saved = self.db.mark_peer_message_delivered(
            message_id,
            delivered=delivered,
            reason=reason,
            delivered_at=delivered_at,
        )
        if not saved:
            return None
        if cache_participants:
            self.append_peer_message_to_caches(saved, emit=emit)
        self.upsert_agent_peer_thread(saved, emit=emit)
        return saved

    def update_peer_message_delivery(
        self,
        message_id: str,
        delivery_state: str,
        *,
        reason: str = "",
        delivered_at: float | None = None,
        emit: bool = True,
        cache_participants: bool = True,
    ) -> dict | None:
        """Persist an explicit peer-message delivery state in participant caches."""
        if not self.db:
            return None
        saved = self.db.update_agent_peer_message_delivery(
            message_id,
            delivery_state,
            reason=reason,
            delivered_at=delivered_at,
        )
        if not saved:
            return None
        if cache_participants:
            self.append_peer_message_to_caches(saved, emit=emit)
        self.upsert_agent_peer_thread(saved, emit=emit)
        return saved

    def _agent_peer_thread_participant(self, row: dict, field: str) -> dict:
        agent_id = str((row or {}).get(f"{field}_id", "") or "").strip()
        row_group = str(
            (row or {}).get("group_name", (row or {}).get("group", "")) or ""
        )
        cell = self.agents.get(agent_id)
        kind = (
            str(getattr(cell, "kind", "") or "").strip()
            if cell else ""
        ) or str((row or {}).get(f"{field}_kind", "") or "").strip()
        name = (
            str(getattr(cell, "name", "") or "").strip()
            if cell else ""
        ) or str((row or {}).get(f"{field}_name", "") or "").strip() or agent_id
        group = (
            str(getattr(cell, "group", "") or "").strip()
            if cell else ""
        ) or row_group
        return {
            "id": agent_id,
            "kind": kind,
            "name": name,
            "group": group,
        }

    def _build_agent_peer_thread(
        self,
        rows: list[dict],
        *,
        message_limit: int = AGENT_PEER_THREAD_MESSAGE_LIMIT,
    ) -> dict | None:
        scoped = [
            dict(row)
            for row in (rows or [])
            if _is_agent_peer_thread_row(row)
        ]
        if not scoped:
            return None
        scoped.sort(
            key=lambda row: (
                _peer_message_timestamp(row),
                str((row or {}).get("id", "") or ""),
            )
        )
        first = scoped[0]
        thread_id = _agent_peer_thread_pair_key(first)
        if not thread_id:
            return None
        pair_ids = list(_agent_peer_thread_pair_ids(first))
        if not pair_ids[0] or not pair_ids[1]:
            return None
        scoped = [
            row for row in scoped
            if list(_agent_peer_thread_pair_ids(row)) == pair_ids
        ]
        if not scoped:
            return None
        participants_by_id: dict[str, dict] = {}
        for row in scoped:
            for field in ("sender", "recipient"):
                participant = self._agent_peer_thread_participant(row, field)
                pid = str(participant.get("id", "") or "").strip()
                if not pid or pid not in pair_ids:
                    continue
                existing = participants_by_id.get(pid, {})
                merged = dict(existing)
                for key, value in participant.items():
                    if str(value or "").strip():
                        merged[key] = value
                    elif key not in merged:
                        merged[key] = value
                participants_by_id[pid] = merged
        participants: list[dict] = [
            participants_by_id.get(pid)
            or {"id": pid, "kind": "", "name": pid, "group": ""}
            for pid in pair_ids
        ]
        participant_ids = list(pair_ids)

        messages = [_agent_peer_thread_message_entry(row) for row in scoped]
        last_row = max(
            scoped,
            key=lambda row: (
                _peer_message_timestamp(row),
                str((row or {}).get("id", "") or ""),
            ),
        )
        last_message = _agent_peer_thread_message_entry(last_row)
        group = str(last_row.get("group_name", last_row.get("group", "")) or "")
        if not group:
            for row in scoped:
                group = str(row.get("group_name", row.get("group", "")) or "")
                if group:
                    break
        ack_required_count = sum(
            1 for row in scoped if bool(row.get("ack_required", False))
        )
        pending_delivery_count = sum(
            1 for row in scoped
            if str(row.get("delivery_state", "") or "buffered").strip()
            == "buffered"
        )
        requires_reply_participant_ids: list[str] = []
        for row in scoped:
            if not bool(row.get("ack_required", False)):
                continue
            recipient_id = str(row.get("recipient_id", "") or "").strip()
            if recipient_id and recipient_id not in requires_reply_participant_ids:
                requires_reply_participant_ids.append(recipient_id)
        title_participants = sorted(
            participants,
            key=lambda participant: (
                (
                    str((participant or {}).get("name", "") or "").strip()
                    or str((participant or {}).get("id", "") or "").strip()
                ).casefold(),
                str((participant or {}).get("id", "") or "").strip(),
            ),
        )
        names = [
            str((participant or {}).get("name", "") or "").strip()
            or str((participant or {}).get("id", "") or "").strip()
            for participant in title_participants
        ]
        if len(names) >= 2:
            title = f"{names[0]} ↔ {names[1]}"
            if len(names) > 2:
                title += f" +{len(names) - 2}"
        elif names:
            title = names[0]
        else:
            title = thread_id

        limit = max(1, int(message_limit or AGENT_PEER_THREAD_MESSAGE_LIMIT))
        truncated = len(messages) > limit
        return {
            "thread_id": thread_id,
            "group": group,
            "title": title,
            "participants": participants,
            "participant_ids": participant_ids,
            "last_activity_at": _peer_message_timestamp(last_row),
            "last_message_id": last_message["id"],
            "last_message": last_message,
            "message_count": len(messages),
            "ack_required_count": ack_required_count,
            "pending_delivery_count": pending_delivery_count,
            "requires_reply_participant_ids": requires_reply_participant_ids,
            "messages": messages[-limit:],
            "truncated": truncated,
        }

    def _sorted_agent_peer_threads(self, threads: dict[str, dict]) -> dict[str, dict]:
        return dict(sorted(
            ((str(tid or ""), dict(thread)) for tid, thread in threads.items()
             if tid and thread),
            key=lambda item: (
                _safe_float((item[1] or {}).get("last_activity_at", 0)),
                str((item[1] or {}).get("last_message_id", "") or ""),
                item[0],
            ),
            reverse=True,
        ))

    def agent_peer_threads_snapshot(
        self,
        *,
        message_limit: int = AGENT_PEER_THREAD_MESSAGE_LIMIT,
    ) -> dict[str, dict]:
        """Return an ordered, bounded copy of the agent↔agent thread aggregate."""
        snapshot: dict[str, dict] = {}
        limit = max(1, int(message_limit or AGENT_PEER_THREAD_MESSAGE_LIMIT))
        for thread_id, thread in self._sorted_agent_peer_threads(
                self.agent_peer_threads).items():
            item = copy.deepcopy(thread)
            messages = list(item.get("messages", []) or [])
            item["messages"] = messages[-limit:]
            item["truncated"] = bool(item.get("truncated", False)) or (
                int(item.get("message_count", len(messages)) or 0) > len(
                    item["messages"]
                )
            )
            snapshot[thread_id] = item
        return snapshot

    def seed_agent_peer_threads(
        self,
        *,
        limit: int = AGENT_PEER_THREAD_SEED_ROW_LIMIT,
        emit: bool = False,
    ) -> int:
        """Seed the read-only Chat panel thread aggregate from SQLite."""
        if not self.db:
            return 0
        loader = getattr(self.db, "load_recent_agent_peer_chat_messages", None)
        if not callable(loader):
            return 0
        by_pair: dict[str, list[dict]] = {}
        for row in loader(limit=limit):
            if not _is_agent_peer_thread_row(row):
                continue
            pair_key = _agent_peer_thread_pair_key(row)
            if not pair_key:
                continue
            by_pair.setdefault(pair_key, []).append(row)
        threads: dict[str, dict] = {}
        for pair_key, rows in by_pair.items():
            thread = self._build_agent_peer_thread(rows)
            if thread:
                threads[pair_key] = thread
        self.agent_peer_threads = self._sorted_agent_peer_threads(threads)
        if emit:
            for pair_key, thread in self.agent_peer_threads.items():
                self._emit(
                    "agent_peer_thread_upsert",
                    thread_id=pair_key,
                    group=thread.get("group", ""),
                    thread=copy.deepcopy(thread),
                )
        return len(self.agent_peer_threads)

    def upsert_agent_peer_thread(
        self,
        row: dict,
        *,
        emit: bool = True,
    ) -> dict | None:
        """Refresh and optionally emit a complete thread replacement."""
        if not _is_agent_peer_thread_row(row or {}):
            return None
        pair_key = _agent_peer_thread_pair_key(row or {})
        first_id, second_id = _agent_peer_thread_pair_ids(row or {})
        if not pair_key or not first_id or not second_id:
            return None
        rows: list[dict]
        loader = (
            getattr(self.db, "load_agent_peer_chat_messages_for_pair", None)
            if self.db else None
        )
        if callable(loader):
            rows = loader(
                first_id,
                second_id,
                limit=AGENT_PEER_THREAD_SEED_ROW_LIMIT,
            )
        else:
            rows = [dict(row)]
        thread = self._build_agent_peer_thread(rows)
        if not thread:
            self.remove_agent_peer_thread(pair_key, emit=emit)
            return None
        self.agent_peer_threads[pair_key] = thread
        self.agent_peer_threads = self._sorted_agent_peer_threads(
            self.agent_peer_threads
        )
        if emit:
            self._emit(
                "agent_peer_thread_upsert",
                thread_id=pair_key,
                group=thread.get("group", ""),
                thread=copy.deepcopy(thread),
            )
        return thread

    def remove_agent_peer_thread(
        self,
        thread_id: str,
        *,
        emit: bool = True,
    ) -> bool:
        tid = str(thread_id or "").strip()
        if not tid or tid not in self.agent_peer_threads:
            return False
        self.agent_peer_threads.pop(tid, None)
        if emit:
            self._emit("agent_peer_thread_remove", thread_id=tid)
        return True

    def _direct_message_agent_ids(self, row: dict) -> list[str]:
        ids: list[str] = []
        for field in ("sender", "recipient"):
            kind = str((row or {}).get(f"{field}_kind", "") or "").strip()
            agent_id = str((row or {}).get(f"{field}_id", "") or "").strip()
            if kind == "user" or not agent_id or agent_id in ids:
                continue
            ids.append(agent_id)
        return ids

    def _upsert_direct_message_cache_entry(
        self,
        agent_id: str,
        entry: dict,
        *,
        limit: int = DIRECT_MESSAGE_CACHE_LIMIT,
    ) -> bool:
        aid = str(agent_id or "").strip()
        if not aid or not entry or not entry.get("id"):
            return False
        message_id = str(entry.get("id", "") or "").strip()
        before = [
            dict(item)
            for item in self.direct_messages_by_agent.get(aid, [])
        ]
        kept = [
            dict(item)
            for item in self.direct_messages_by_agent.get(aid, [])
            if str((item or {}).get("id", "") or "").strip() != message_id
        ]
        kept.append(dict(entry))
        sorted_entries = _sort_direct_message_cache(kept)
        if len(sorted_entries) > max(1, limit):
            sorted_entries = sorted_entries[-max(1, limit):]
        self.direct_messages_by_agent[aid] = sorted_entries
        return before != sorted_entries

    def refresh_direct_message_cache_for_agent(
        self,
        agent_id: str,
        *,
        limit: int = DIRECT_MESSAGE_CACHE_LIMIT,
        emit: bool = True,
    ) -> list[dict]:
        """Rebuild one agent's bounded direct-message cache from SQLite."""
        aid = str(agent_id or "").strip()
        if not aid:
            return []
        cell = self.agents.get(aid)
        if not cell:
            return []
        if not self.db:
            return [
                dict(entry)
                for entry in self.direct_messages_by_agent.get(aid, [])
            ][:limit]
        rows = self.db.load_direct_messages_for_agent(aid, limit=limit)
        entries = [
            entry
            for entry in (
                _direct_message_cache_entry(row, aid) for row in rows
            )
            if entry
        ]
        entries = _sort_direct_message_cache(entries)
        before = [
            dict(item)
            for item in self.direct_messages_by_agent.get(aid, [])
        ]
        self.direct_messages_by_agent[aid] = entries[-max(1, limit):]
        if emit and before != self.direct_messages_by_agent[aid]:
            for entry in self.direct_messages_by_agent[aid]:
                self._emit(
                    "direct_message_upsert",
                    id=entry["id"],
                    agent_id=aid,
                    group=str(getattr(cell, "group", "") or ""),
                    message=dict(entry),
                    limit=limit,
                )
        return self.direct_messages_by_agent[aid]

    def seed_direct_message_caches(
        self,
        *,
        limit: int = DIRECT_MESSAGE_CACHE_LIMIT,
        emit: bool = False,
    ) -> int:
        """Seed recent direct-message/display rows after restart/load."""
        seeded = 0
        for cell in list(self.agents.values()):
            if str(getattr(cell, "cell_type", "") or "") != "agent":
                continue
            entries = self.refresh_direct_message_cache_for_agent(
                cell.id,
                limit=limit,
                emit=emit,
            )
            if entries:
                seeded += 1
        return seeded

    def direct_messages_snapshot(
        self,
        *,
        limit: int = DIRECT_MESSAGE_CACHE_LIMIT,
    ) -> dict[str, list[dict]]:
        """Return a bounded copy of the per-agent direct-message cache."""
        snapshot: dict[str, list[dict]] = {}
        for agent_id, entries in self.direct_messages_by_agent.items():
            aid = str(agent_id or "").strip()
            if not aid:
                continue
            bounded = _sort_direct_message_cache([
                dict(entry) for entry in (entries or [])
            ])[-max(1, limit):]
            snapshot[aid] = bounded
        return snapshot

    def append_direct_message_to_caches(
        self,
        row: dict,
        *,
        emit: bool = True,
        limit: int = DIRECT_MESSAGE_CACHE_LIMIT,
    ) -> list[str]:
        """Project a canonical direct-message row into participant caches."""
        if not _is_user_direct_message_row(row or {}):
            return []
        changed: list[str] = []
        for agent_id in self._direct_message_agent_ids(row or {}):
            cell = self.agents.get(agent_id)
            entry = _direct_message_cache_entry(row or {}, agent_id)
            if not cell or not entry:
                continue
            if self._upsert_direct_message_cache_entry(
                agent_id,
                entry,
                limit=limit,
            ):
                changed.append(agent_id)
                if emit:
                    self._emit(
                        "direct_message_upsert",
                        id=entry["id"],
                        agent_id=agent_id,
                        group=entry.get("group", ""),
                        message=dict(entry),
                        limit=limit,
                    )
        return changed

    def save_direct_message(
        self,
        row: dict,
        *,
        emit: bool = True,
    ) -> dict | None:
        """Persist a direct message and update bounded live UI caches."""
        if not self.db:
            return None
        saved = self.db.save_direct_message(row)
        if saved:
            self.append_direct_message_to_caches(saved, emit=emit)
            cloud_hooks.notify_direct_message_observers(
                "direct_message_saved",
                saved,
                state=self,
            )
        return saved

    def update_direct_message_delivery(
        self,
        message_id: str,
        delivery_state: str,
        *,
        reason: str = "",
        delivered_at: float | None = None,
        emit: bool = True,
    ) -> dict | None:
        """Persist direct-message delivery state and update live caches."""
        if not self.db:
            return None
        saved = self.db.update_direct_message_delivery(
            message_id,
            delivery_state,
            reason=reason,
            delivered_at=delivered_at,
        )
        if not saved:
            return None
        self.append_direct_message_to_caches(saved, emit=emit)
        cloud_hooks.notify_direct_message_observers(
            "direct_message_delivery_updated",
            saved,
            state=self,
        )
        return saved

    def mark_direct_message_delivered(
        self,
        message_id: str,
        *,
        delivered: bool = True,
        reason: str = "",
        delivered_at: float | None = None,
        emit: bool = True,
    ) -> dict | None:
        """Convenience wrapper for delivered/failed direct messages."""
        return self.update_direct_message_delivery(
            message_id,
            "delivered" if delivered else "failed",
            reason="" if delivered else reason,
            delivered_at=delivered_at,
            emit=emit,
        )

    def mark_direct_message_read(
        self,
        message_id: str,
        *,
        read_at: float | None = None,
        reader_id: str = "",
        emit: bool = True,
    ) -> dict | None:
        """Persist direct-message UI read state without changing delivery."""
        if not self.db:
            return None
        saved = self.db.mark_direct_message_read(
            message_id,
            read_at=read_at,
            reader_id=reader_id,
        )
        if not saved:
            return None
        changed = []
        for agent_id in self._direct_message_agent_ids(saved):
            entry = _direct_message_cache_entry(saved, agent_id)
            if not entry:
                continue
            if self._upsert_direct_message_cache_entry(agent_id, entry):
                changed.append((agent_id, entry))
        if emit:
            for agent_id, entry in changed:
                self._emit(
                    "direct_message_read",
                    id=entry["id"],
                    agent_id=agent_id,
                    group=entry.get("group", ""),
                    read_at=entry.get("read_at", 0),
                    message=dict(entry),
                )
        cloud_hooks.notify_direct_message_observers(
            "direct_message_read",
            saved,
            state=self,
        )
        return saved

    def _normalize_agent_message_history_entry(self, entry: dict) -> dict:
        return {
            "id": int(_safe_float(entry.get("id"), 0)),
            "agent_id": str(entry.get("agent_id", "") or "").strip(),
            "message": str(entry.get("message", "") or ""),
            "sent_at": _safe_float(entry.get("sent_at")),
        }

    def agent_message_history_read(
            self, agent_id: str,
            limit: int = AGENT_MESSAGE_HISTORY_LIMIT) -> list[dict]:
        """Return newest-first user message recall entries for one agent."""
        aid = str(agent_id or "").strip()
        if not aid:
            return []
        try:
            limit = max(1, min(int(limit or AGENT_MESSAGE_HISTORY_LIMIT), 1000))
        except (TypeError, ValueError):
            limit = AGENT_MESSAGE_HISTORY_LIMIT
        if self.db:
            try:
                rows = self.db.load_agent_message_history(aid, limit=limit)
                history = [
                    self._normalize_agent_message_history_entry(row)
                    for row in rows
                ]
                self.agent_message_history[aid] = history[
                    :AGENT_MESSAGE_HISTORY_LIMIT
                ]
                return history
            except Exception:
                log.exception("Failed to load message history for %s", aid)
        return [
            self._normalize_agent_message_history_entry(row)
            for row in self.agent_message_history.get(aid, [])[:limit]
        ]

    def agent_message_history_snapshot(
            self, limit: int = AGENT_MESSAGE_HISTORY_LIMIT) -> dict[str, list[dict]]:
        """Return bounded newest-first recall history for cells in state."""
        snapshot: dict[str, list[dict]] = {}
        for aid in self.agents:
            entries = self.agent_message_history_read(aid, limit=limit)
            if entries:
                snapshot[aid] = entries
        return snapshot

    def record_message_history(self, agent_id: str, message: str) -> dict | None:
        """Persist and publish a user-sent message for per-agent recall."""
        aid = str(agent_id or "").strip()
        text = str(message or "")
        if not aid or not text.strip():
            return None
        ts = time.time()
        entry = {
            "id": int(ts * 1000000),
            "agent_id": aid,
            "message": text,
            "sent_at": ts,
        }
        if self.db:
            try:
                entry = self.db.save_agent_message_history(entry)
            except Exception:
                log.exception("Failed to record message history for %s", aid)
                return None
        entry = self._normalize_agent_message_history_entry(entry)
        history = self.agent_message_history.setdefault(aid, [])
        history.insert(0, entry)
        del history[AGENT_MESSAGE_HISTORY_LIMIT:]
        self._emit(
            "agent_message_history_append",
            agent_id=aid,
            entry=entry,
            limit=AGENT_MESSAGE_HISTORY_LIMIT,
        )
        return entry

    def history_record_agent(self, cell: AgentCell):
        """Record a new agent in the history table."""
        if not self.db:
            return
        import time
        try:
            self.db.save_agent_history({
                "id": cell.id,
                "name": cell.name,
                "slug": cell.slug,
                "group": cell.group,
                "agent_type": cell.agent_type,
                "template": cell.template,
                "created_at": time.time(),
                "worktree_branch": cell.worktree_branch,
                "status": "active",
            })
        except Exception:
            log.exception("Failed to record agent history %s", cell.id)

    def history_remove_agent(self, cell: AgentCell):
        """Mark an agent as removed in history, snapshot final tokens."""
        if not self.db:
            return
        import time
        try:
            # Read current totals and add session tokens
            rec = self.db.load_agent_history_detail(cell.id)
            prev_in = rec["total_tokens_in"] if rec else 0
            prev_out = rec["total_tokens_out"] if rec else 0
            already_removed = bool(rec and rec.get("removed_at"))
            fields = {
                "removed_at": (rec or {}).get("removed_at") or time.time(),
                "total_tokens_in": (
                    prev_in if already_removed
                    else prev_in + cell.session_tokens_in
                ),
                "total_tokens_out": (
                    prev_out if already_removed
                    else prev_out + cell.session_tokens_out
                ),
            }
            if (rec or {}).get("status") != "merged":
                fields["status"] = "removed"
            self.db.update_agent_history(cell.id, **fields)
        except Exception:
            log.exception("Failed to update agent history on remove %s",
                          cell.id)

    def history_update_agent(self, cell: AgentCell, **fields):
        """Update arbitrary fields on an agent's history record."""
        if not self.db:
            return
        try:
            self.db.update_agent_history(cell.id, **fields)
        except Exception:
            log.exception("Failed to update agent history %s", cell.id)

    def history_record_dispatch(self, cell: AgentCell, task: BoardTask, *,
                                engineer_group: str = "",
                                engineer_id: str = ""):
        """Record a task dispatch in history."""
        import time
        ts = time.time()
        if cell.mark_progress(ts):
            self._emit_agent(cell)
        engineer_group = str(engineer_group or "").strip()
        engineer_id = str(engineer_id or "").strip()
        try:
            self._db_save_agent(cell)
            if self.db:
                self.db.save_agent_task({
                    "agent_id": cell.id,
                    "task_id": task.id,
                    "task_title": task.task,
                    "started_at": ts,
                })
                self.db.update_agent_history(
                    cell.id, total_tasks=(
                        self.db.load_agent_history_detail(cell.id) or {}
                    ).get("total_tasks", 0) + 1)
            if engineer_group:
                entry = {
                    "group": engineer_group,
                    "task_id": task.id,
                    "task_title": task.task,
                    "agent_id": cell.id,
                    "agent_name": cell.name,
                    "agent_slug": cell.slug,
                    "agent_owned": bool(
                        engineer_id and cell.created_by_engineer_id == engineer_id
                    ),
                    "started_at": ts,
                }
                if self.db:
                    entry["id"] = self.db.save_engineer_task_log_entry(entry)
                    self.db.trim_engineer_task_log(
                        engineer_group,
                        limit=_ENGINEER_WORKLOG_LIMIT,
                    )
                else:
                    entries = self.engineer_worklog.get(engineer_group, [])
                    newest_id = entries[0]["id"] if entries else 0
                    entry["id"] = int(newest_id or 0) + 1
                self._append_engineer_worklog_entry(engineer_group, entry)
        except Exception:
            log.exception("Failed to record dispatch history %s → %s",
                          cell.id, task.id)

    def history_record_message(self, cell_id: str, action: str,
                               message: str, task_id: str = "",
                               *, mark_progress: bool = True):
        """Record an agent message (torque ai report) in history."""
        import time
        ts = time.time()
        cell = self.agents.get(cell_id)
        if cell:
            if mark_progress and cell.mark_progress(ts):
                self._emit_agent(cell)
            if mark_progress:
                self._db_save_agent(cell)
        if not self.db:
            return
        try:
            self.db.save_agent_message({
                "agent_id": cell_id,
                "task_id": task_id,
                "timestamp": ts,
                "action": action,
                "message": message,
            })
        except Exception:
            log.exception("Failed to record agent message %s/%s",
                          cell_id, action)

    def history_complete_task(self, agent_id: str, task_id: str,
                              outcome: str):
        """Mark an agent-task association as completed."""
        if not self.db:
            return
        import time
        try:
            self.db.update_agent_task(
                agent_id, task_id,
                completed_at=time.time(), outcome=outcome)
        except Exception:
            log.exception("Failed to complete agent task %s/%s",
                          agent_id, task_id)

    def history_snapshot_tokens(self, cell: AgentCell):
        """Snapshot current session tokens into history totals."""
        if not self.db or not (cell.session_tokens_in
                               or cell.session_tokens_out):
            return
        try:
            rec = self.db.load_agent_history_detail(cell.id)
            if not rec:
                return
            self.db.update_agent_history(
                cell.id,
                total_tokens_in=(rec["total_tokens_in"]
                                 + cell.session_tokens_in),
                total_tokens_out=(rec["total_tokens_out"]
                                  + cell.session_tokens_out),
            )
        except Exception:
            log.exception("Failed to snapshot tokens %s", cell.id)

    def history_reconcile_tombstoned_agents(self) -> int:
        """Correct legacy history rows for tombstoned agents."""
        if not self.db:
            return 0
        reconciled = 0
        for cell in list(self.agents.values()):
            if cell.cell_type != "agent" or not self.agent_is_tombstoned(cell):
                continue
            rec = self.db.load_agent_history_detail(cell.id)
            if not rec:
                continue
            if rec.get("removed_at") and rec.get("status") != "active":
                continue
            self.history_remove_agent(cell)
            reconciled += 1
        return reconciled
