"""Digest delivery, per-agent digest settings, and journal persistence."""

import json

from torque.persistence.common import snapshot_db_payload as _snapshot_db_payload

def _digest_event_json(event: dict) -> str:
    """Encode a digest event without EngineerEventBuffer private metadata."""
    payload = {
        str(key): value
        for key, value in dict(event or {}).items()
        if not str(key).startswith("_digest_")
    }
    return json.dumps(payload, separators=(",", ":"))

def _decode_digest_event_json(raw: str) -> dict:
    try:
        event = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        event = {}
    return event if isinstance(event, dict) else {}


class DigestPersistenceMixin:
    """Persist digest queues, delivery state, settings, and journals."""

    def save_digest_queued_event(
        self,
        recipient_id: str,
        event: dict,
        enqueued_at: float,
    ) -> int:
        """Persist one queued digest event and return its queue row ID."""
        def _operation():
            try:
                cur = self._conn.execute(
                    "INSERT INTO digest_queued_events "
                    "(recipient_id, event_json, enqueued_at) VALUES (?,?,?)",
                    (
                        str(recipient_id or ""),
                        _digest_event_json(event),
                        float(enqueued_at or 0),
                    ),
                )
                self._conn.commit()
                return int(cur.lastrowid or 0)
            except Exception:
                self._conn.rollback()
                raise

        return self._run_sqlite_write_with_lock_retry(
            _operation,
            surface="digest",
        )

    def load_digest_queued_events(self) -> dict[str, list[dict]]:
        """Load queued digest events grouped by recipient in delivery order."""
        rows = self._conn.execute(
            "SELECT id, recipient_id, event_json, enqueued_at "
            "FROM digest_queued_events "
            "ORDER BY recipient_id, id"
        ).fetchall()
        result: dict[str, list[dict]] = {}
        for row_id, recipient_id, event_json, enqueued_at in rows:
            recipient_id = str(recipient_id or "")
            if not recipient_id:
                continue
            event = _decode_digest_event_json(event_json)
            event["_digest_queue_id"] = int(row_id or 0)
            event["_digest_enqueued_at"] = float(enqueued_at or 0)
            result.setdefault(recipient_id, []).append(event)
        return result

    def delete_digest_queued_events(self, recipient_id: str) -> int:
        """Delete all queued digest events for one recipient."""
        recipient_id = str(recipient_id or "")
        if not recipient_id:
            return 0

        def _operation():
            try:
                cur = self._conn.execute(
                    "DELETE FROM digest_queued_events WHERE recipient_id=?",
                    (recipient_id,),
                )
                self._conn.commit()
                return int(cur.rowcount or 0)
            except Exception:
                self._conn.rollback()
                raise

        return self._run_sqlite_write_with_lock_retry(
            _operation,
            surface="digest",
        )

    def load_digest_sent_events(
        self,
        *,
        limit_per_recipient: int = 200,
    ) -> dict[str, list[dict]]:
        """Load recent sent digest events grouped by recipient.

        Each recipient is capped in memory to the most recent
        ``limit_per_recipient`` rows, returned oldest-first for UI display.
        """
        cap = max(0, int(limit_per_recipient or 0))
        rows = self._conn.execute(
            "SELECT recipient_id, event_json, enqueued_at, delivered_at "
            "FROM digest_sent_events "
            "ORDER BY recipient_id, delivered_at DESC, id DESC"
        ).fetchall()
        newest_first: dict[str, list[dict]] = {}
        for recipient_id, event_json, enqueued_at, delivered_at in rows:
            recipient_id = str(recipient_id or "")
            if not recipient_id:
                continue
            bucket = newest_first.setdefault(recipient_id, [])
            if cap and len(bucket) >= cap:
                continue
            event = _decode_digest_event_json(event_json)
            event["delivered_at"] = float(
                event.get("delivered_at") or delivered_at or 0
            )
            bucket.append(event)

        result: dict[str, list[dict]] = {}
        for recipient_id, events in newest_first.items():
            result[recipient_id] = list(reversed(events))
        return result

    def complete_digest_delivery(
        self,
        recipient_id: str,
        sent_events: list[tuple[dict, float, float]],
        queue_ids: list[int],
        *,
        sent_cap: int = 200,
    ):
        """Move delivered events from the queued table to sent history.

        ``sent_events`` contains ``(event, enqueued_at, delivered_at)`` tuples.
        Queue deletion, sent inserts, and sent-history pruning are committed as
        one transaction so a mid-flush crash cannot leave a partial move.
        """
        recipient_id = str(recipient_id or "")
        if not recipient_id:
            return
        clean_queue_ids = []
        for row_id in queue_ids or []:
            try:
                row_id = int(row_id or 0)
            except (TypeError, ValueError):
                row_id = 0
            if row_id > 0:
                clean_queue_ids.append(row_id)
        sent_rows = [
            (
                recipient_id,
                _digest_event_json(event),
                float(enqueued_at or 0),
                float(delivered_at or 0),
            )
            for event, enqueued_at, delivered_at in (sent_events or [])
        ]

        def _operation():
            try:
                self._conn.execute("BEGIN")
                if sent_rows:
                    self._conn.executemany(
                        "INSERT INTO digest_sent_events "
                        "(recipient_id, event_json, enqueued_at, delivered_at) "
                        "VALUES (?,?,?,?)",
                        sent_rows,
                    )
                for i in range(0, len(clean_queue_ids), 500):
                    chunk = clean_queue_ids[i:i + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    self._conn.execute(
                        "DELETE FROM digest_queued_events "
                        f"WHERE recipient_id=? AND id IN ({placeholders})",
                        (recipient_id, *chunk),
                    )
                self._prune_digest_sent_events_uncommitted(
                    recipient_id,
                    keep=sent_cap,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        self._run_sqlite_write_with_lock_retry(_operation, surface="digest")

    def prune_digest_sent_events(self, recipient_id: str, *, keep: int = 200):
        """Keep only the newest sent digest events for one recipient."""
        with self._conn:
            self._prune_digest_sent_events_uncommitted(
                str(recipient_id or ""),
                keep=keep,
            )

    def _prune_digest_sent_events_uncommitted(
        self,
        recipient_id: str,
        *,
        keep: int,
    ):
        if not recipient_id:
            return
        keep = max(0, int(keep or 0))
        row = self._conn.execute(
            "SELECT COUNT(*) FROM digest_sent_events WHERE recipient_id=?",
            (recipient_id,),
        ).fetchone()
        count = int((row[0] if row else 0) or 0)
        excess = count - keep
        if excess <= 0:
            return
        self._conn.execute(
            "DELETE FROM digest_sent_events WHERE id IN ("
            "SELECT id FROM digest_sent_events "
            "WHERE recipient_id=? "
            "ORDER BY delivered_at ASC, id ASC "
            "LIMIT ?"
            ")",
            (recipient_id, excess),
        )

    # -- Engineer settings & journal -------------------------------------------

    def save_engineer_settings(self, group_name: str, settings: dict):
        """Upsert engineer settings for a group."""
        enabled_events = json.dumps(
            settings.get("enabled_events",
                         ["agent_started", "task_dispatched",
                          "task_derived", "task_health_alert"]))
        self._conn.execute("""
            INSERT OR REPLACE INTO engineer_settings
                (group_name, push_interval, max_interval, heartbeat_interval,
                 default_worker_concurrency, autonomy_mode,
                 wave_size_preference, same_agent_follow_up_preference,
                 digest_verbosity, escalation_style,
                 paused,
                 custom_instructions, restrict_to_created_agents,
                 pending_question, pending_note,
                 pending_note_kind, pending_note_set_at,
                 pending_note_actor_id, enabled_events,
                 engineer_provider, engineer_boot_command,
                 engineer_model, engineer_reasoning_effort, engineer_fast_mode,
                 engineer_directory, engineer_profile,
                 engineer_shell, engineer_tab_color,
                 pending_question_set_at,
                 pending_question_actor_id,
                 engineer_can_override_worker_provider)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            group_name,
            settings.get("push_interval", 60),
            settings.get("max_interval", 300),
            settings.get("heartbeat_interval",
                         settings.get("max_interval", 300)),
            settings.get("default_worker_concurrency", 2),
            settings.get("autonomy_mode", "dispatch_when_clear"),
            settings.get("wave_size_preference", "small"),
            settings.get("same_agent_follow_up_preference", "balanced"),
            settings.get("digest_verbosity", "balanced"),
            settings.get("escalation_style", "note_then_ask"),
            1 if settings.get("paused", False) else 0,
            settings.get("custom_instructions", ""),
            1 if settings.get("restrict_to_created_agents", False) else 0,
            settings.get("pending_question", ""),
            settings.get("pending_note", ""),
            settings.get("pending_note_kind", ""),
            float(settings.get("pending_note_set_at", 0) or 0),
            settings.get("pending_note_actor_id", ""),
            enabled_events,
            settings.get("engineer_provider", ""),
            settings.get("engineer_boot_command", ""),
            settings.get("engineer_model", ""),
            settings.get("engineer_reasoning_effort", ""),
            settings.get("engineer_fast_mode", "inherit"),
            settings.get("engineer_directory", ""),
            settings.get("engineer_profile", ""),
            settings.get("engineer_shell", ""),
            settings.get("engineer_tab_color", ""),
            float(settings.get("pending_question_set_at", 0) or 0),
            settings.get("pending_question_actor_id", ""),
            1 if settings.get(
                "engineer_can_override_worker_provider", True) else 0,
        ))
        self._conn.commit()

    async def save_engineer_settings_async(self, group_name: str, settings: dict):
        """Queue and await a engineer-settings save without blocking the event loop."""
        return await self._enqueue_async_write(
            "engineer_settings",
            "save_engineer_settings",
            group_name,
            _snapshot_db_payload(settings or {}),
        )

    def save_agent_digest_settings(
        self,
        agent_id: str,
        settings: dict,
        *,
        commit: bool = True,
    ) -> None:
        """Upsert per-agent digest settings."""
        enabled_events = json.dumps(
            settings.get(
                "enabled_events",
                [
                    "agent_started",
                    "task_dispatched",
                    "task_derived",
                    "task_health_alert",
                ],
            )
        )
        self._conn.execute(
            """
            INSERT OR REPLACE INTO agent_digest_settings
                (agent_id, paused, push_interval, max_interval,
                 heartbeat_interval, digest_verbosity, enabled_events,
                 architect_digest, wake_on_digest, suppress_empty)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                agent_id,
                1 if settings.get("paused", False) else 0,
                settings.get("push_interval", 60),
                settings.get("max_interval", 300),
                settings.get(
                    "heartbeat_interval",
                    settings.get("max_interval", 300),
                ),
                settings.get("digest_verbosity", "balanced"),
                enabled_events,
                1 if settings.get("architect_digest", False) else 0,
                1 if settings.get("wake_on_digest", False) else 0,
                1 if settings.get("suppress_empty", False) else 0,
            ),
        )
        if commit:
            self._conn.commit()

    def load_engineer_settings(self, group_name: str) -> dict | None:
        """Load engineer settings for a group. Returns None if not set."""
        row = self._conn.execute(
            "SELECT group_name, push_interval, max_interval, heartbeat_interval, "
            "default_worker_concurrency, autonomy_mode, "
            "wave_size_preference, same_agent_follow_up_preference, "
            "digest_verbosity, escalation_style, paused, "
            "custom_instructions, restrict_to_created_agents, "
            "pending_question, pending_note, pending_note_kind, "
            "pending_note_set_at, pending_note_actor_id, enabled_events, "
            "engineer_provider, engineer_boot_command, "
            "engineer_model, engineer_reasoning_effort, engineer_fast_mode, "
            "engineer_directory, engineer_profile, "
            "engineer_shell, engineer_tab_color, "
            "pending_question_set_at, "
            "pending_question_actor_id, "
            "engineer_can_override_worker_provider "
            "FROM engineer_settings "
            "WHERE group_name=?", (group_name,)).fetchone()
        if not row:
            return None
        try:
            enabled = json.loads(row[18])
        except (json.JSONDecodeError, TypeError):
            enabled = [
                "agent_started",
                "task_dispatched",
                "task_derived",
                "task_health_alert",
            ]
        heartbeat_interval = row[3]
        if heartbeat_interval is None or (
                heartbeat_interval == 300 and row[2] != 300):
            heartbeat_interval = row[2]
        return {
            "group": row[0],
            "push_interval": row[1],
            "max_interval": row[2],
            "heartbeat_interval": heartbeat_interval,
            "default_worker_concurrency": row[4],
            "autonomy_mode": row[5],
            "wave_size_preference": row[6] if len(row) > 6 else "small",
            "same_agent_follow_up_preference": (
                row[7] if len(row) > 7 else "balanced"
            ),
            "digest_verbosity": row[8] if len(row) > 8 else "balanced",
            "escalation_style": row[9] if len(row) > 9 else "note_then_ask",
            "paused": bool(row[10]),
            "custom_instructions": row[11],
            "restrict_to_created_agents": bool(row[12]),
            "pending_question": row[13],
            "pending_note": row[14],
            "pending_note_kind": row[15],
            "pending_note_set_at": row[16] if len(row) > 16 else 0.0,
            "pending_note_actor_id": row[17] if len(row) > 17 else "",
            "enabled_events": enabled,
            "engineer_provider": row[19] if len(row) > 19 else "",
            "engineer_boot_command": row[20] if len(row) > 20 else "",
            "engineer_model": row[21] if len(row) > 21 else "",
            "engineer_reasoning_effort": row[22] if len(row) > 22 else "",
            "engineer_fast_mode": row[23] if len(row) > 23 else "inherit",
            "engineer_directory": row[24] if len(row) > 24 else "",
            "engineer_profile": row[25] if len(row) > 25 else "",
            "engineer_shell": row[26] if len(row) > 26 else "",
            "engineer_tab_color": row[27] if len(row) > 27 else "",
            "pending_question_set_at": row[28] if len(row) > 28 else 0.0,
            "pending_question_actor_id": row[29] if len(row) > 29 else "",
            "engineer_can_override_worker_provider": (
                bool(row[30]) if len(row) > 30 else True
            ),
        }

    def load_agent_digest_settings(self, agent_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT agent_id, paused, push_interval, max_interval, "
            "heartbeat_interval, digest_verbosity, enabled_events, "
            "architect_digest, wake_on_digest "
            "FROM agent_digest_settings WHERE agent_id=?",
            (agent_id,),
        ).fetchone()
        if not row:
            return None
        try:
            enabled = json.loads(row[6])
        except (json.JSONDecodeError, TypeError):
            enabled = [
                "agent_started",
                "task_dispatched",
                "task_derived",
                "task_health_alert",
            ]
        heartbeat_interval = row[4]
        if heartbeat_interval is None or (
                heartbeat_interval == 300 and row[3] != 300):
            heartbeat_interval = row[3]
        return {
            "agent_id": row[0],
            "paused": bool(row[1]),
            "push_interval": row[2],
            "max_interval": row[3],
            "heartbeat_interval": heartbeat_interval,
            "digest_verbosity": row[5] if len(row) > 5 else "balanced",
            "enabled_events": enabled,
            "architect_digest": bool(row[7]) if len(row) > 7 else False,
            "wake_on_digest": bool(row[8]) if len(row) > 8 else False,
        }

    def delete_engineer_settings(self, group_name: str):
        self._conn.execute(
            "DELETE FROM engineer_settings WHERE group_name=?", (group_name,))
        self._conn.commit()

    def agent_exists(self, agent_id: str) -> bool:
        """Return whether an agent/cell row exists in SQLite."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM agents WHERE id=? LIMIT 1",
            (agent_id,),
        ).fetchone()
        return bool(row)

    def delete_agent_digest_settings(self, agent_id: str):
        self._conn.execute(
            "DELETE FROM agent_digest_settings WHERE agent_id=?",
            (agent_id,),
        )
        self._conn.commit()

    def load_all_engineer_settings(self) -> dict[str, dict]:
        """Load engineer settings for all groups. Returns {group: settings}."""
        rows = self._conn.execute(
            "SELECT group_name, push_interval, max_interval, heartbeat_interval, "
            "default_worker_concurrency, autonomy_mode, "
            "wave_size_preference, same_agent_follow_up_preference, "
            "digest_verbosity, escalation_style, paused, "
            "custom_instructions, restrict_to_created_agents, "
            "pending_question, pending_note, pending_note_kind, "
            "pending_note_set_at, pending_note_actor_id, enabled_events, "
            "engineer_provider, engineer_boot_command, "
            "engineer_model, engineer_reasoning_effort, engineer_fast_mode, "
            "engineer_directory, engineer_profile, "
            "engineer_shell, engineer_tab_color, "
            "pending_question_set_at, "
            "pending_question_actor_id, "
            "engineer_can_override_worker_provider "
            "FROM engineer_settings"
        ).fetchall()
        result = {}
        for row in rows:
            try:
                enabled = json.loads(row[18])
            except (json.JSONDecodeError, TypeError):
                enabled = [
                    "agent_started",
                    "task_dispatched",
                    "task_derived",
                    "task_health_alert",
                ]
            heartbeat_interval = row[3]
            if heartbeat_interval is None or (
                    heartbeat_interval == 300 and row[2] != 300):
                heartbeat_interval = row[2]
            result[row[0]] = {
                "group": row[0],
                "push_interval": row[1],
                "max_interval": row[2],
                "heartbeat_interval": heartbeat_interval,
                "default_worker_concurrency": row[4],
                "autonomy_mode": row[5],
                "wave_size_preference": row[6] if len(row) > 6 else "small",
                "same_agent_follow_up_preference": (
                    row[7] if len(row) > 7 else "balanced"
                ),
                "digest_verbosity": row[8] if len(row) > 8 else "balanced",
                "escalation_style": row[9] if len(row) > 9 else "note_then_ask",
                "paused": bool(row[10]),
                "custom_instructions": row[11],
                "restrict_to_created_agents": bool(row[12]),
                "pending_question": row[13],
                "pending_note": row[14],
                "pending_note_kind": row[15],
                "pending_note_set_at": row[16] if len(row) > 16 else 0.0,
                "pending_note_actor_id": row[17] if len(row) > 17 else "",
                "enabled_events": enabled,
                "engineer_provider": row[19] if len(row) > 19 else "",
                "engineer_boot_command": row[20] if len(row) > 20 else "",
                "engineer_model": row[21] if len(row) > 21 else "",
                "engineer_reasoning_effort": row[22] if len(row) > 22 else "",
                "engineer_fast_mode": row[23] if len(row) > 23 else "inherit",
                "engineer_directory": row[24] if len(row) > 24 else "",
                "engineer_profile": row[25] if len(row) > 25 else "",
                "engineer_shell": row[26] if len(row) > 26 else "",
                "engineer_tab_color": row[27] if len(row) > 27 else "",
                "pending_question_set_at": row[28] if len(row) > 28 else 0.0,
                "pending_question_actor_id": row[29] if len(row) > 29 else "",
                "engineer_can_override_worker_provider": (
                    bool(row[30]) if len(row) > 30 else True
                ),
            }
        return result

    def load_all_agent_digest_settings(self) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT agent_id, paused, push_interval, max_interval, "
            "heartbeat_interval, digest_verbosity, enabled_events, "
            "architect_digest, wake_on_digest, suppress_empty "
            "FROM agent_digest_settings"
        ).fetchall()
        result = {}
        for row in rows:
            try:
                enabled = json.loads(row[6])
            except (json.JSONDecodeError, TypeError):
                enabled = [
                    "agent_started",
                    "task_dispatched",
                    "task_derived",
                    "task_health_alert",
                ]
            heartbeat_interval = row[4]
            if heartbeat_interval is None or (
                    heartbeat_interval == 300 and row[3] != 300):
                heartbeat_interval = row[3]
            result[row[0]] = {
                "agent_id": row[0],
                "paused": bool(row[1]),
                "push_interval": row[2],
                "max_interval": row[3],
                "heartbeat_interval": heartbeat_interval,
                "digest_verbosity": row[5] if len(row) > 5 else "balanced",
                "enabled_events": enabled,
                "architect_digest": bool(row[7]) if len(row) > 7 else False,
                "wake_on_digest": bool(row[8]) if len(row) > 8 else False,
                "suppress_empty": bool(row[9]) if len(row) > 9 else False,
            }
        return result

    def save_journal_entry(self, group_name: str, timestamp: float,
                           entry_type: str, entry: str,
                           author_cell_id: str = "",
                           source_key: str = "",
                           return_inserted: bool = False):
        """Insert a engineer journal entry. Returns the new row ID."""
        author_cell_id = str(author_cell_id or "").strip()
        source_key = str(source_key or "").strip()
        inserted = True
        c = self._conn.execute(
            "INSERT OR IGNORE INTO engineer_journal "
            "(group_name, timestamp, entry_type, entry, author_cell_id, "
            "source_key) VALUES (?,?,?,?,?,?)",
            (group_name, timestamp, entry_type, entry, author_cell_id,
             source_key))
        if source_key and c.rowcount == 0:
            inserted = False
            row = self._conn.execute(
                "SELECT id FROM engineer_journal WHERE group_name=? "
                "AND author_cell_id=? AND source_key=?",
                (group_name, author_cell_id, source_key),
            ).fetchone()
            entry_id = int(row[0]) if row else 0
        else:
            entry_id = int(c.lastrowid or 0)
        self._conn.commit()
        if return_inserted:
            return entry_id, inserted
        return entry_id

    def load_journal_entries(self, group_name: str, limit: int = 20,
                             entry_type: str = "",
                             author_cell_id: str = "") -> list[dict]:
        """Load recent journal entries for a group, newest first."""
        filters = ["group_name=?"]
        params = [group_name]
        if entry_type:
            filters.append("entry_type=?")
            params.append(entry_type)
        author_cell_id = str(author_cell_id or "").strip()
        if author_cell_id:
            filters.append("author_cell_id=?")
            params.append(author_cell_id)
        params.append(limit)
        rows = self._conn.execute(
            "SELECT id, group_name, timestamp, entry_type, entry, "
            "author_cell_id FROM engineer_journal WHERE "
            + " AND ".join(filters)
            + " ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        return [{"id": r[0], "group": r[1], "timestamp": r[2],
                 "type": r[3], "entry": r[4],
                 "author_cell_id": r[5]} for r in rows]
