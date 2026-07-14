"""Architect journal and Engineer worklog state orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

_ENGINEER_WORKLOG_LIMIT = 200


def _safe_journal_filename(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return token or "architect"


class JournalService:
    def __init__(self, state: Any):
        self._state = state

    def _architect_journal_path(self, architect_id: str) -> Path:
        return Path(self._state.journal_data_dir) / "architect_journals" / (
            _safe_journal_filename(architect_id) + ".jsonl"
        )

    def _architect_journal_entry_id(self, architect_id: str,
                                    idempotency_key: str) -> str:
        digest = hashlib.sha256(
            f"{architect_id}:{idempotency_key}".encode("utf-8")
        ).hexdigest()
        return digest[:12]

    def _recover_architect_journal_entry(self, architect_id: str, *,
                                         record_id: str,
                                         request_hash: str = "") -> dict | None:
        for existing in self.architect_journal_read(
            architect_id,
            limit=1_000_000,
        ):
            if str((existing or {}).get("id", "") or "") != record_id:
                continue
            existing_hash = str(
                (existing or {}).get("request_hash", "") or ""
            ).strip()
            if request_hash and existing_hash and existing_hash != request_hash:
                raise ValueError(
                    "Idempotency key was reused for a different architect journal append"
                )
            return existing
        return None

    def architect_journal_append(self, architect_id: str, entry_type: str,
                                 entry: str, *,
                                 idempotency_key: str = "",
                                 request_hash: str = "") -> dict:
        """Append one architect journal entry to its JSONL file."""
        import time

        architect_id = str(architect_id or "").strip()
        if not architect_id:
            raise ValueError("architect_id is required")
        idem_key = str(idempotency_key or "").strip()
        request_hash = str(request_hash or "").strip()
        if idem_key:
            receipt = self._state.db.load_command_receipt(idem_key) if self._state.db else None
            if receipt:
                existing_hash = str(receipt.get("request_hash", "") or "").strip()
                if request_hash and existing_hash and existing_hash != request_hash:
                    raise ValueError(
                        "Idempotency key was reused for a different architect journal append"
                    )
                response = receipt.get("response")
                if isinstance(response, dict):
                    return response
            recovered = self._recover_architect_journal_entry(
                architect_id,
                record_id=self._architect_journal_entry_id(
                    architect_id,
                    idem_key,
                ),
                request_hash=request_hash,
            )
            if recovered:
                if self._state.db:
                    self._state.db.save_command_receipt(
                        idempotency_key=idem_key,
                        surface="internal",
                        command_name="architect_journal_append",
                        request_hash=request_hash,
                        response=recovered,
                    )
                return recovered
        record = {
            "id": (
                self._architect_journal_entry_id(architect_id, idem_key)
                if idem_key else uuid.uuid4().hex[:12]
            ),
            "architect_id": architect_id,
            "timestamp": time.time(),
            "type": str(entry_type or "").strip(),
            "entry": str(entry or ""),
        }
        if request_hash:
            record["request_hash"] = request_hash
        path = self._architect_journal_path(architect_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if idem_key and self._state.db:
            self._state.db.save_command_receipt(
                idempotency_key=idem_key,
                surface="internal",
                command_name="architect_journal_append",
                request_hash=request_hash,
                response=record,
            )
        self._state._emit("architect_journal_append", **record)
        return record

    def architect_journal_read(self, architect_id: str, *,
                               since: float = 0,
                               limit: int = 20) -> list[dict]:
        """Read recent architect journal entries, newest first."""
        architect_id = str(architect_id or "").strip()
        if not architect_id:
            return []
        try:
            since_value = float(since or 0)
        except (TypeError, ValueError):
            since_value = 0.0
        try:
            limit_value = int(limit or 20)
        except (TypeError, ValueError):
            limit_value = 20
        if limit_value <= 0:
            return []

        path = self._architect_journal_path(architect_id)
        if not path.exists():
            return []

        entries = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = str(line or "").strip()
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(item, dict):
                    continue
                item_architect_id = str(
                    item.get("architect_id", architect_id) or architect_id
                ).strip()
                if item_architect_id != architect_id:
                    continue
                try:
                    timestamp = float(item.get("timestamp", 0) or 0)
                except (TypeError, ValueError):
                    timestamp = 0.0
                if since_value and timestamp <= since_value:
                    continue
                item["architect_id"] = architect_id
                item["timestamp"] = timestamp
                entries.append(item)
        if len(entries) > limit_value:
            entries = entries[-limit_value:]
        entries.reverse()
        return entries

    def _append_engineer_worklog_entry(self, group: str, entry: dict):
        """Append a Engineer worklog entry to in-memory state and emit it."""
        if not group:
            return
        item = dict(entry or {})
        item["group"] = group
        entries = self._state.engineer_worklog.setdefault(group, [])
        entries.insert(0, item)
        if len(entries) > _ENGINEER_WORKLOG_LIMIT:
            del entries[_ENGINEER_WORKLOG_LIMIT:]
        self._state._emit("engineer_worklog_append", group=group, entry=dict(item))

    def engineer_worklog_read(self, group: str, limit: int = 50) -> list[dict]:
        """Return recent persisted/current designated-engineer worklog entries for a group."""
        entries = self._state.engineer_worklog.get(group, [])
        if limit <= 0:
            return []
        return [dict(entry) for entry in entries[:limit]]
