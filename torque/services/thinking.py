"""Thinking domain orchestration for Scratchpad notes."""

from __future__ import annotations

from typing import Any

from ..config import log


class ThinkingService:
    def __init__(self, state: Any):
        self._state = state

    def thinking_snapshot(self, *, group: str = "",
                          include_archived: bool = False) -> dict:
        if not self._state.db:
            return {"scratchpad_notes": {}}
        try:
            notes = self._state.db.list_scratchpad_notes(
                group=group,
                include_archived=include_archived,
                include_deleted=False,
                limit=1000,
            )
            return {
                "scratchpad_notes": {
                    str(note.get("id", "") or ""): note
                    for note in notes
                    if str(note.get("id", "") or "")
                },
            }
        except Exception:
            log.exception("Failed to load thinking snapshot")
            return {"scratchpad_notes": {}}

    def _emit_scratchpad_note(self, note: dict | None) -> None:
        if not note:
            return
        self._state._emit("thinking_scratchpad_note_upsert", **dict(note))

    def resolve_scratchpad_note_id(self, identifier: str, *,
                                   group: str = "") -> str:
        ident = str(identifier or "").strip()
        if not ident or not self._state.db:
            return ""
        note = self._state.db.load_scratchpad_note(ident)
        if note:
            return str(note.get("id", "") or "")
        group = str(group or "").strip()
        ident_lower = ident.lower()
        try:
            for item in self._state.db.list_scratchpad_notes(
                    group=group,
                    include_archived=True,
                    include_deleted=True,
                    limit=1000):
                if str(item.get("slug", "") or "").lower() == ident_lower:
                    return str(item.get("id", "") or "")
        except Exception:
            log.exception("Failed to resolve scratchpad note %s", ident)
        return ""

    def list_scratchpad_notes(self, *, group: str = "",
                              include_archived: bool = False,
                              include_deleted: bool = False,
                              limit: int = 200) -> list[dict]:
        if not self._state.db:
            return []
        try:
            return self._state.db.list_scratchpad_notes(
                group=group,
                include_archived=include_archived,
                include_deleted=include_deleted,
                limit=limit,
            )
        except Exception:
            log.exception("Failed to list scratchpad notes")
            return []

    def load_scratchpad_note(self, note_id: str) -> dict | None:
        if not self._state.db:
            return None
        try:
            return self._state.db.load_scratchpad_note(note_id)
        except Exception:
            log.exception("Failed to load scratchpad note %s", note_id)
            return None

    async def create_scratchpad_note_async(self, row_dict: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            note = await self._state.db.create_scratchpad_note_async(row_dict)
            self._emit_scratchpad_note(note)
            return note
        except Exception:
            log.exception("Failed to create scratchpad note")
            raise
    async def update_scratchpad_note_async(self, note_id: str,
                                           patch: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            note = await self._state.db.update_scratchpad_note_async(note_id, patch)
            self._emit_scratchpad_note(note)
            return note
        except Exception:
            log.exception("Failed to update scratchpad note %s", note_id)
            raise

    async def archive_scratchpad_note_async(self, note_id: str,
                                            **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            note = await self._state.db.archive_scratchpad_note_async(note_id, **kwargs)
            self._emit_scratchpad_note(note)
            return note
        except Exception:
            log.exception("Failed to archive scratchpad note %s", note_id)
            raise

    async def delete_scratchpad_note_async(self, note_id: str,
                                           **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            note = await self._state.db.delete_scratchpad_note_async(note_id, **kwargs)
            self._emit_scratchpad_note(note)
            return note
        except Exception:
            log.exception("Failed to delete scratchpad note %s", note_id)
            raise
