"""SQLite persistence for architect decisions and pending hires."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .common import (
    json_loads_default as _json_loads_default,
    snapshot_db_payload as _snapshot_db_payload,
)


def _decision_json_list(value) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            value = []
    if not isinstance(value, list):
        return []
    out = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out


def _decision_json_dict(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            value = {}
    return dict(value or {}) if isinstance(value, dict) else {}


def _decision_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default or 0)


def _decode_decision_row(row, cols) -> dict:
    decision = dict(zip(cols, row))
    decision["supersedes"] = (
        str(decision.get("supersedes", "") or "").strip() or None
    )
    decision["linked_task_ids"] = _decision_json_list(
        decision.get("linked_task_ids", "[]")
    )
    decision["linked_engineer_ids"] = _decision_json_list(
        decision.get("linked_engineer_ids", "[]")
    )
    decision["metadata"] = _decision_json_dict(
        decision.get("metadata_json", decision.get("metadata", "{}"))
    )
    decision.pop("metadata_json", None)
    decision["archived"] = bool(decision.get("archived", 0))
    decision["created_at"] = _decision_int(decision.get("created_at", 0))
    decision["updated_at"] = _decision_int(decision.get("updated_at", 0))
    return decision


def _decode_pending_hire_row(row, cols) -> dict:
    pending_hire = dict(zip(cols, row))
    raw_specs = pending_hire.get("requested_specializations", "[]")
    specs = _json_loads_default(raw_specs, [])
    pending_hire["requested_specializations"] = [
        str(item or "").strip()
        for item in specs
        if str(item or "").strip()
    ]
    pending_hire["created_at"] = _decision_int(
        pending_hire.get("created_at", 0)
    )
    pending_hire["resolved_at"] = _decision_int(
        pending_hire.get("resolved_at", 0)
    )
    return pending_hire


class ArchitectGovernancePersistenceMixin:
    """TorqueDB API for architect decisions and pending hires."""

    def load_decision(self, decision_id: str) -> dict | None:
        """Load one persisted architect decision by id."""
        decision_id = str(decision_id or "").strip()
        if not decision_id:
            return None
        cursor = self._conn.execute(
            "SELECT id, architect_id, title, rationale, status, supersedes, "
            "linked_task_ids, linked_engineer_ids, archived, created_at, updated_at, metadata_json "
            "FROM decisions WHERE id=?",
            (decision_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return _decode_decision_row(row, cols)

    def save_decision(self, row_dict: dict) -> dict:
        """Upsert one architect decision and return the normalized row."""
        row = dict(row_dict or {})
        decision_id = str(row.get("id", "") or "").strip()
        if not decision_id:
            raise ValueError("decision id is required")

        existing = self.load_decision(decision_id) or {}
        now_ts = int(datetime.now(timezone.utc).timestamp())
        architect_id = str(
            row.get("architect_id", existing.get("architect_id", "")) or ""
        ).strip()
        if not architect_id:
            raise ValueError("architect_id is required")

        created_at = _decision_int(
            row.get("created_at", existing.get("created_at", now_ts)),
            now_ts,
        )
        updated_at = _decision_int(
            row.get("updated_at", now_ts),
            now_ts,
        )
        supersedes = row.get("supersedes", existing.get("supersedes", None))
        supersedes = str(supersedes or "").strip() or None
        linked_task_ids = _decision_json_list(
            row.get(
                "linked_task_ids",
                existing.get("linked_task_ids", []),
            )
        )
        linked_engineer_ids = _decision_json_list(
            row.get(
                "linked_engineer_ids",
                existing.get("linked_engineer_ids", []),
            )
        )
        archived = bool(row.get("archived", existing.get("archived", False)))
        metadata = _decision_json_dict(
            row.get("metadata", row.get("metadata_json", existing.get("metadata", {})))
        )

        self._conn.execute(
            "INSERT OR REPLACE INTO decisions "
            "(id, architect_id, title, rationale, status, supersedes, "
            "linked_task_ids, linked_engineer_ids, archived, created_at, updated_at, metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision_id,
                architect_id,
                str(row.get("title", existing.get("title", "")) or ""),
                str(row.get("rationale", existing.get("rationale", "")) or ""),
                str(row.get("status", existing.get("status", "proposed")) or "proposed"),
                supersedes,
                json.dumps(linked_task_ids),
                json.dumps(linked_engineer_ids),
                1 if archived else 0,
                created_at,
                updated_at,
                json.dumps(metadata),
            ),
        )
        self._conn.commit()
        saved = self.load_decision(decision_id)
        if not saved:
            raise RuntimeError(f"failed to load saved decision {decision_id}")
        return saved

    async def save_decision_async(self, row_dict: dict) -> dict:
        """Queue and await a decision save without blocking the event loop."""
        return await self._enqueue_async_write(
            "decisions",
            "save_decision",
            _snapshot_db_payload(row_dict or {}),
        )

    def load_decisions_for_architect(self, architect_id: str, *,
                                     include_archived: bool = False) -> list[dict]:
        """Load persisted decisions for one architect, newest first."""
        architect_id = str(architect_id or "").strip()
        if not architect_id:
            return []
        query = (
            "SELECT id, architect_id, title, rationale, status, supersedes, "
            "linked_task_ids, linked_engineer_ids, archived, created_at, updated_at, metadata_json "
            "FROM decisions WHERE architect_id=?"
        )
        params = [architect_id]
        if not include_archived:
            query += " AND archived=0"
        query += " ORDER BY updated_at DESC, created_at DESC, id DESC"
        cursor = self._conn.execute(query, tuple(params))
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [_decode_decision_row(row, cols) for row in rows]

    def load_all_decisions(self, *, include_archived: bool = False) -> list[dict]:
        """Load all persisted architect decisions, newest first."""
        query = (
            "SELECT id, architect_id, title, rationale, status, supersedes, "
            "linked_task_ids, linked_engineer_ids, archived, created_at, updated_at, metadata_json "
            "FROM decisions"
        )
        if not include_archived:
            query += " WHERE archived=0"
        query += " ORDER BY updated_at DESC, created_at DESC, id DESC"
        cursor = self._conn.execute(query)
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [_decode_decision_row(row, cols) for row in rows]

    def delete_decision(self, decision_id: str) -> dict | None:
        """Soft-delete one decision by marking it archived."""
        decision_id = str(decision_id or "").strip()
        if not decision_id:
            return None
        existing = self.load_decision(decision_id)
        if not existing:
            return None
        self._conn.execute(
            "UPDATE decisions SET archived=1, updated_at=? WHERE id=?",
            (int(datetime.now(timezone.utc).timestamp()), decision_id),
        )
        self._conn.commit()
        return self.load_decision(decision_id)

    def hard_delete_decision(self, decision_id: str) -> None:
        """Permanently delete one decision row."""
        decision_id = str(decision_id or "").strip()
        if not decision_id:
            return
        self._conn.execute("DELETE FROM decisions WHERE id=?", (decision_id,))
        self._conn.commit()
    def load_pending_hire(self, hire_id: str) -> dict | None:
        """Load one persisted pending hire by id."""
        hire_id = str(hire_id or "").strip()
        if not hire_id:
            return None
        cursor = self._conn.execute(
            "SELECT id, architect_id, requested_name, requested_command, "
            "requested_provider, requested_directory, "
            "requested_specializations, status, resolution_note, "
            "created_at, resolved_at, created_engineer_id "
            "FROM pending_hires WHERE id=?",
            (hire_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return _decode_pending_hire_row(row, cols)

    def save_pending_hire(self, row_dict: dict) -> dict:
        """Upsert one pending-hire row and return the normalized row."""
        row = dict(row_dict or {})
        hire_id = str(row.get("id", "") or "").strip()
        if not hire_id:
            raise ValueError("pending hire id is required")

        existing = self.load_pending_hire(hire_id) or {}
        now_ts = int(datetime.now(timezone.utc).timestamp())
        architect_id = str(
            row.get("architect_id", existing.get("architect_id", "")) or ""
        ).strip()
        if not architect_id:
            raise ValueError("architect_id is required")
        requested_name = str(
            row.get("requested_name", existing.get("requested_name", "")) or ""
        ).strip()
        if not requested_name:
            raise ValueError("requested_name is required")

        status = str(
            row.get("status", existing.get("status", "pending")) or "pending"
        ).strip() or "pending"
        if status not in {"pending", "approved", "rejected"}:
            raise ValueError(
                "status must be one of: pending, approved, rejected"
            )
        created_at = _decision_int(
            row.get("created_at", existing.get("created_at", now_ts)),
            now_ts,
        )
        prior_status = str(existing.get("status", "") or "").strip()
        if status == "pending":
            resolved_at = 0
        elif "resolved_at" in row:
            resolved_at = _decision_int(row.get("resolved_at", now_ts), now_ts)
        elif prior_status == status and existing.get("resolved_at", 0):
            resolved_at = _decision_int(existing.get("resolved_at", 0), now_ts)
        else:
            resolved_at = now_ts

        requested_specializations = _json_loads_default(
            row.get(
                "requested_specializations",
                existing.get("requested_specializations", []),
            ),
            [],
        )
        requested_specializations = [
            str(item or "").strip()
            for item in requested_specializations
            if str(item or "").strip()
        ]

        self._conn.execute(
            "INSERT OR REPLACE INTO pending_hires "
            "(id, architect_id, requested_name, requested_command, "
            "requested_provider, requested_directory, "
            "requested_specializations, status, resolution_note, "
            "created_at, resolved_at, created_engineer_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                hire_id,
                architect_id,
                requested_name,
                str(
                    row.get(
                        "requested_command",
                        existing.get("requested_command", ""),
                    ) or ""
                ),
                str(
                    row.get(
                        "requested_provider",
                        existing.get("requested_provider", ""),
                    ) or ""
                ),
                str(
                    row.get(
                        "requested_directory",
                        existing.get("requested_directory", ""),
                    ) or ""
                ),
                json.dumps(requested_specializations),
                status,
                str(
                    row.get(
                        "resolution_note",
                        existing.get("resolution_note", ""),
                    ) or ""
                ),
                created_at,
                resolved_at,
                str(
                    row.get(
                        "created_engineer_id",
                        existing.get("created_engineer_id", ""),
                    ) or ""
                ),
            ),
        )
        self._conn.commit()
        saved = self.load_pending_hire(hire_id)
        if not saved:
            raise RuntimeError(f"failed to load saved pending hire {hire_id}")
        return saved

    async def save_pending_hire_async(self, row_dict: dict) -> dict:
        """Queue and await a pending-hire save without blocking the event loop."""
        return await self._enqueue_async_write(
            "pending_hires",
            "save_pending_hire",
            _snapshot_db_payload(row_dict or {}),
        )

    def load_pending_hires(self, *, status_filter: str = "",
                           architect_id: str = "") -> list[dict]:
        """Load persisted pending-hire rows, newest first."""
        query = (
            "SELECT id, architect_id, requested_name, requested_command, "
            "requested_provider, requested_directory, "
            "requested_specializations, status, resolution_note, "
            "created_at, resolved_at, created_engineer_id "
            "FROM pending_hires WHERE 1=1"
        )
        params: list = []
        architect_id = str(architect_id or "").strip()
        if architect_id:
            query += " AND architect_id=?"
            params.append(architect_id)
        status_filter = str(status_filter or "").strip()
        if status_filter:
            query += " AND status=?"
            params.append(status_filter)
        query += " ORDER BY created_at DESC, id DESC"
        cursor = self._conn.execute(query, tuple(params))
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [_decode_pending_hire_row(row, cols) for row in rows]

    def delete_pending_hire(self, hire_id: str) -> None:
        """Permanently delete one pending-hire row."""
        hire_id = str(hire_id or "").strip()
        if not hire_id:
            return
        self._conn.execute("DELETE FROM pending_hires WHERE id=?", (hire_id,))
        self._conn.commit()
