"""Architect decision and pending-hire state orchestration."""

from __future__ import annotations

from typing import Any

from ..config import log


class ArchitectGovernanceService:
    def __init__(self, state: Any):
        self._state = state

    def load_decision(self, decision_id: str) -> dict | None:
        """Load one persisted architect decision."""
        if self._state.db:
            try:
                return self._state.db.load_decision(decision_id)
            except Exception:
                log.exception("Failed to load decision %s", decision_id)
        return None

    def save_decision(self, row_dict: dict) -> dict | None:
        """Persist one architect decision and return the normalized row."""
        if self._state.db:
            try:
                saved = self._state.db.save_decision(row_dict)
                self._state._emit_decision(saved)
                return saved
            except Exception:
                log.exception(
                    "Failed to save decision %s",
                    str((row_dict or {}).get("id", "") or ""),
                )
        return None

    async def save_decision_async(self, row_dict: dict) -> dict | None:
        """Persist one architect decision off the event loop."""
        if self._state.db:
            try:
                saved = await self._state.db.save_decision_async(row_dict)
                self._state._emit_decision(saved)
                return saved
            except Exception:
                log.exception(
                    "Failed to save decision %s",
                    str((row_dict or {}).get("id", "") or ""),
                )
        return None

    def load_decisions_for_architect(self, architect_id: str, *,
                                     include_archived: bool = False) -> list[dict]:
        """Load persisted decisions for one architect."""
        if self._state.db:
            try:
                return self._state.db.load_decisions_for_architect(
                    architect_id,
                    include_archived=include_archived,
                )
            except Exception:
                log.exception(
                    "Failed to load decisions for architect %s",
                    architect_id,
                )
        return []

    def load_all_decisions(self, *, include_archived: bool = False) -> list[dict]:
        """Load all persisted architect decisions."""
        if self._state.db:
            try:
                return self._state.db.load_all_decisions(
                    include_archived=include_archived,
                )
            except Exception:
                log.exception("Failed to load decisions")
        return []

    def delete_decision(self, decision_id: str) -> dict | None:
        """Soft-delete one architect decision."""
        if self._state.db:
            try:
                deleted = self._state.db.delete_decision(decision_id)
                self._state._emit_decision(deleted)
                return deleted
            except Exception:
                log.exception("Failed to delete decision %s", decision_id)
        return None

    def hard_delete_decision(self, decision_id: str) -> None:
        """Permanently delete one architect decision."""
        if self._state.db:
            try:
                self._state.db.hard_delete_decision(decision_id)
                self._state._emit("decision_remove", id=str(decision_id or "").strip())
            except Exception:
                log.exception("Failed to hard-delete decision %s", decision_id)

    def load_pending_hire(self, hire_id: str) -> dict | None:
        """Load one persisted pending hire."""
        if self._state.db:
            try:
                return self._state.db.load_pending_hire(hire_id)
            except Exception:
                log.exception("Failed to load pending hire %s", hire_id)
        return None

    def save_pending_hire(self, row_dict: dict) -> dict | None:
        """Persist one pending-hire row and emit the matching delta."""
        if self._state.db:
            try:
                saved = self._state.db.save_pending_hire(row_dict)
                self._state._emit_pending_hire(saved)
                return saved
            except Exception:
                log.exception(
                    "Failed to save pending hire %s",
                    str((row_dict or {}).get("id", "") or ""),
                )
        return None

    async def save_pending_hire_async(self, row_dict: dict) -> dict | None:
        """Persist one pending-hire row off the event loop."""
        if self._state.db:
            try:
                saved = await self._state.db.save_pending_hire_async(row_dict)
                self._state._emit_pending_hire(saved)
                return saved
            except Exception:
                log.exception(
                    "Failed to save pending hire %s",
                    str((row_dict or {}).get("id", "") or ""),
                )
        return None

    def load_pending_hires(self, *, status_filter: str = "",
                           architect_id: str = "") -> list[dict]:
        """Load pending-hire rows from persistence."""
        if self._state.db:
            try:
                return self._state.db.load_pending_hires(
                    status_filter=status_filter,
                    architect_id=architect_id,
                )
            except Exception:
                log.exception(
                    "Failed to load pending hires status=%s architect=%s",
                    status_filter,
                    architect_id,
                )
        return []

    def delete_pending_hire(self, hire_id: str) -> None:
        """Permanently delete one pending-hire row."""
        if self._state.db:
            try:
                self._state.db.delete_pending_hire(hire_id)
                self._state._emit("pending_hire_resolve", id=str(hire_id or "").strip())
            except Exception:
                log.exception("Failed to delete pending hire %s", hire_id)
