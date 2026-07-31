"""Idea Brief domain orchestration and Thinking-link validation."""

from __future__ import annotations

from typing import Any

from ..config import log


class IdeaBriefService:
    def __init__(self, state: Any):
        self._state = state

    def idea_brief_snapshot(self, *, group: str = "",
                            include_archived: bool = False) -> dict:
        if not self._state.db:
            return {}
        try:
            return {
                str(brief.get("id", "") or ""): brief
                for brief in self._state.db.list_idea_briefs(
                    group=group,
                    include_archived=include_archived,
                    limit=1000,
                )
                if str(brief.get("id", "") or "")
            }
        except Exception:
            log.exception("Failed to load idea brief snapshot")
            return {}

    def _emit_idea_brief(self, brief: dict | None) -> None:
        if not brief:
            return
        self._state._emit("idea_brief_upsert", **dict(brief))

    def resolve_idea_brief_id(self, identifier: str, *,
                              group: str = "") -> str:
        ident = str(identifier or "").strip()
        if not ident or not self._state.db:
            return ""
        brief = self._state.db.load_idea_brief(ident)
        if brief:
            return str(brief.get("id", "") or "")
        group = str(group or "").strip()
        ident_lower = ident.lower()
        try:
            for item in self._state.db.list_idea_briefs(
                    group=group,
                    include_archived=True,
                    limit=1000):
                if str(item.get("slug", "") or "").lower() == ident_lower:
                    return str(item.get("id", "") or "")
        except Exception:
            log.exception("Failed to resolve idea brief %s", ident)
        return ""

    def list_idea_briefs(self, *, group: str = "",
                         status: str = "",
                         include_archived: bool = False,
                         created_by_id: str = "",
                         limit: int = 200) -> list[dict]:
        if not self._state.db:
            return []
        try:
            return self._state.db.list_idea_briefs(
                group=group,
                status=status,
                include_archived=include_archived,
                created_by_id=created_by_id,
                limit=limit,
            )
        except Exception:
            log.exception("Failed to list idea briefs")
            return []

    def count_idea_briefs(self, *, group: str = "",
                          status: str = "",
                          include_archived: bool = False,
                          created_by_id: str = "") -> int:
        if not self._state.db:
            return 0
        try:
            return self._state.db.count_idea_briefs(
                group=group,
                status=status,
                include_archived=include_archived,
                created_by_id=created_by_id,
            )
        except Exception:
            log.exception("Failed to count idea briefs")
            return 0

    def load_idea_brief(self, brief_id: str) -> dict | None:
        if not self._state.db:
            return None
        try:
            return self._state.db.load_idea_brief(brief_id)
        except Exception:
            log.exception("Failed to load idea brief %s", brief_id)
            return None

    def _idea_brief_link_error(self, message: str) -> ValueError:
        return ValueError(message)

    def normalize_idea_brief_thinking_links(self, links, *,
                                            group: str) -> list[dict]:
        """Resolve and validate Idea Brief links to Thinking artifacts.

        Links stay lightweight but carry stable references plus enough source
        metadata for later UI/review traceability.  Missing, deleted, or
        cross-group Thinking references fail closed.
        """

        group = str(group or "").strip()
        if not links:
            return []
        if not isinstance(links, list):
            raise self._idea_brief_link_error("thinking_links must be a list")
        normalized = []
        seen: set[tuple[str, str]] = set()
        for raw in links:
            if isinstance(raw, str):
                item = {"id": raw}
            elif isinstance(raw, dict):
                item = dict(raw)
            else:
                raise self._idea_brief_link_error(
                    "thinking_links entries must be objects or ids"
                )
            raw_type = str(
                item.get("type", item.get("kind", item.get("artifact_type", "")))
                or ""
            ).strip().lower().replace("-", "_")
            ident = str(
                item.get("id", "")
                or item.get("ref", "")
                or item.get("note_id", "")
                or ""
            ).strip()
            if not ident:
                raise self._idea_brief_link_error("thinking link id is required")
            if not raw_type and "-S:" in ident:
                raw_type = "scratchpad_note"
            if raw_type in {"scratchpad", "note", "scratchpad_note"}:
                note_id = self._state.resolve_scratchpad_note_id(ident, group=group)
                note = self._state.load_scratchpad_note(note_id)
                if (
                        not note
                        or note.get("deleted")
                        or str(note.get("group_name", "") or "").strip() != group):
                    raise self._idea_brief_link_error(
                        f"Scratchpad note not found: {ident}"
                    )
                payload = {
                    "type": "scratchpad_note",
                    "id": str(note.get("id", "") or ""),
                    "group": group,
                    "slug": str(note.get("slug", "") or ""),
                    "title": str(note.get("title", "") or ""),
                    "archived": bool(note.get("archived")),
                }
            else:
                raise self._idea_brief_link_error(
                    f"Unsupported thinking link type: {raw_type or ident}"
                )
            for key in (
                    "context", "source_context", "summary", "reason",
                    "quote", "selection", "note", "confidence"):
                if key in item:
                    payload[key] = item[key]
            dedupe_key = (payload["type"], payload["id"])
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(payload)
        return normalized

    def idea_brief_contract(self) -> dict:
        return idea_brief_contract_metadata()

    async def create_idea_brief_async(self, row_dict: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            row = dict(row_dict or {})
            group = str(row.get("group", row.get("group_name", "")) or "").strip()
            if "thinking_links" in row or "thinking_links_json" in row:
                row["thinking_links"] = self.normalize_idea_brief_thinking_links(
                    row.get("thinking_links", row.get("thinking_links_json", [])),
                    group=group,
                )
            brief = await self._state.db.create_idea_brief_async(row)
            self._emit_idea_brief(brief)
            return brief
        except Exception:
            log.exception("Failed to create idea brief")
            raise
    async def update_idea_brief_async(self, brief_id: str,
                                      patch: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            existing = self.load_idea_brief(brief_id)
            row = dict(patch or {})
            if existing and ("thinking_links" in row or "thinking_links_json" in row):
                row["thinking_links"] = self.normalize_idea_brief_thinking_links(
                    row.get("thinking_links", row.get("thinking_links_json", [])),
                    group=str(existing.get("group_name", "") or ""),
                )
            brief = await self._state.db.update_idea_brief_async(brief_id, row)
            self._emit_idea_brief(brief)
            return brief
        except Exception:
            log.exception("Failed to update idea brief %s", brief_id)
            raise
    async def refine_idea_brief_async(self, brief_id: str,
                                      patch: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            existing = self.load_idea_brief(brief_id)
            row = dict(patch or {})
            if existing and ("thinking_links" in row or "thinking_links_json" in row):
                row["thinking_links"] = self.normalize_idea_brief_thinking_links(
                    row.get("thinking_links", row.get("thinking_links_json", [])),
                    group=str(existing.get("group_name", "") or ""),
                )
            brief = await self._state.db.refine_idea_brief_async(brief_id, row)
            self._emit_idea_brief(brief)
            return brief
        except Exception:
            log.exception("Failed to refine idea brief %s", brief_id)
            raise

    async def park_idea_brief_async(self, brief_id: str,
                                    **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            brief = await self._state.db.park_idea_brief_async(brief_id, **kwargs)
            self._emit_idea_brief(brief)
            return brief
        except Exception:
            log.exception("Failed to park idea brief %s", brief_id)
            raise

    async def archive_idea_brief_async(self, brief_id: str,
                                       **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            brief = await self._state.db.archive_idea_brief_async(brief_id, **kwargs)
            self._emit_idea_brief(brief)
            return brief
        except Exception:
            log.exception("Failed to archive idea brief %s", brief_id)
            raise

    async def propose_idea_brief_async(self, brief_id: str,
                                       **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            brief = await self._state.db.propose_idea_brief_async(brief_id, **kwargs)
            self._emit_idea_brief(brief)
            return brief
        except Exception:
            log.exception("Failed to propose idea brief %s", brief_id)
            raise
